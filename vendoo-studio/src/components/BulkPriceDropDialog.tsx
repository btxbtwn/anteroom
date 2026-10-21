import { useMemo, useState } from "react";
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
import { cn } from "../lib/utils";
import { LazyImage } from "./PhotoThumbs";
import { money, PriceDialog, PriceRow, PriceRows, PriceStatus } from "./PriceDropParts";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";

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

  const dropCount = useMemo(() => markdownDrops(drafts || []).length, [drafts]);
  const ready = drafts !== null && canApplyMarkdowns(drafts);

  const update = (id: string, patch: Partial<MarkdownDraft>) =>
    setEdits((prev) => new Map(prev).set(id, { ...prev.get(id), ...patch }));

  return (
    <PriceDialog
      className="max-w-2xl"
      title={outcomes ? markdownSummary(outcomes) : "Mark down listings"}
      description={outcomes
        ? "Nothing was sent to Vendoo. Each listing now has unsent edits: open it and press Update Vendoo to send the new price to the marketplaces."
        : "Each price starts on the listing's suggestion from your own sales and prior drops. Edit any price or skip a listing. Applying saves the prices in Studio only."}
      busy={apply.isPending}
      onClose={onClose}
      footer={outcomes ? (
        <Button onClick={onClose}>Done</Button>
      ) : (
        <>
          <Button variant="outline" disabled={apply.isPending} onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!ready || apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending ? "Saving…" : applyMarkdownsLabel(dropCount)}
          </Button>
        </>
      )}
    >
      {outcomes ? (
        <PriceRows>
          {outcomes.map((outcome) => (
            <PriceRow key={outcome.id} title={outcome.title} error={outcome.reason !== null}>
              {outcome.reason === null ? (
                <span className="inline-flex items-center gap-1.5">
                  {money(outcome.from)} → {money(outcome.to)}
                  <Badge
                    variant="info"
                    title="Edited in Studio since Vendoo last had this listing. Press Update Vendoo to write it onto the Vendoo form."
                  >
                    unsent
                  </Badge>
                </span>
              ) : (
                outcome.reason
              )}
            </PriceRow>
          ))}
        </PriceRows>
      ) : preview.isError ? (
        <PriceStatus error>
          {(preview.error as Error)?.message || "Could not load the suggestions."}
        </PriceStatus>
      ) : drafts === null ? (
        <PriceStatus>Loading a suggestion for each listing…</PriceStatus>
      ) : (
        <ul className="flex flex-col divide-y overflow-hidden rounded-xl border bg-card">
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
        <PriceStatus error>
          {(apply.error as Error)?.message || "The prices could not be saved."}
        </PriceStatus>
      ) : null}
    </PriceDialog>
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
    <li className={cn("flex items-center gap-3 px-3 py-2", draft.skip && "opacity-64")}>
      <span className="size-9 shrink-0 overflow-hidden rounded-md border bg-muted" aria-hidden="true">
        {draft.coverUrl ? <LazyImage src={draft.coverUrl} className="size-full" /> : null}
      </span>
      <div className="min-w-0 flex-1">
        <label className="block truncate font-medium" htmlFor={inputId}>{draft.title}</label>
        <div className="truncate text-muted-foreground text-xs">
          {draft.unavailable || draft.reason}
        </div>
        {error ? <div className="text-destructive-foreground text-xs">{error}</div> : null}
      </div>
      <div className="flex shrink-0 items-center gap-1.5 text-muted-foreground text-xs tabular-nums">
        <span>{money(draft.current)} →</span>
        <Input
          nativeInput
          size="sm"
          id={inputId}
          className="w-20"
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
        <span className="w-10">{percent}</span>
      </div>
      <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs">
        <Checkbox
          checked={draft.skip}
          disabled={disabled || Boolean(draft.unavailable)}
          onCheckedChange={(checked) => onChange({ skip: checked === true })}
        />
        Skip
      </label>
    </li>
  );
}
