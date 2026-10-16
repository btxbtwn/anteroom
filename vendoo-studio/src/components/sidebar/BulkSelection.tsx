import { BulkRegeneratePriceDialog } from "../BulkRegeneratePriceDialog";
import { BulkPriceDropDialog } from "../BulkPriceDropDialog";
import { RegenerateReviewDialog } from "../RegenerateReviewDialog";
import type { BulkRegenerate, BulkRegenerateRun } from "../useBulkRegenerate";
import type { ListingSelection } from "./useListingSelection";

/** The bar under the list while selecting or while a bulk run is going. */
export function BulkActionBar({
  bulk,
  selection,
  eligibleIds,
}: {
  bulk: BulkRegenerate;
  selection: ListingSelection;
  eligibleIds: string[];
}) {
  const { selectedIds, toggleSelectAll, regenerateSelected, setMarkdownIds, exitSelecting } = selection;
  return (
    <div className="sidebar-bulk-bar" role="region" aria-label="Regenerate listings">
      <p className="sidebar-bulk-count">{bulkStatusLabel(bulk.run, selectedIds.size, eligibleIds.length)}</p>
      <div className="sidebar-bulk-actions">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={bulk.running || eligibleIds.length === 0}
          onClick={toggleSelectAll}
        >
          {eligibleIds.length > 0 && eligibleIds.every((id) => selectedIds.has(id)) ? "None" : "All"}
        </button>
        <button
          type="button"
          className="btn btn-danger btn-sm"
          disabled={bulk.running || selectedIds.size === 0}
          onClick={() => { void regenerateSelected(); }}
        >
          Regenerate
        </button>
        <button
          type="button"
          className="btn btn-outline btn-sm"
          title="Review a lower price for each selected listing. Saves in Studio only; nothing is sent to Vendoo."
          disabled={bulk.running || selectedIds.size === 0}
          onClick={() => setMarkdownIds(eligibleIds.filter((id) => selectedIds.has(id)))}
        >
          Mark down
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={Boolean(bulk.run?.cancelRequested)}
          onClick={() => {
            if (bulk.running) bulk.cancel();
            else exitSelecting();
          }}
        >
          {bulk.running ? (bulk.run?.cancelRequested ? "Finishing…" : "Stop batch") : "Cancel"}
        </button>
      </div>
    </div>
  );
}

/** The price, Mark down and review dialogs a bulk action opens. */
export function BulkSelectionDialogs({ selection }: { selection: ListingSelection }) {
  const {
    pricePrompt,
    setPricePrompt,
    reviewPrompt,
    setReviewPrompt,
    markdownIds,
    setMarkdownIds,
    exitSelecting,
    finishRegenerate,
  } = selection;
  return (
    <>
      {pricePrompt ? (
        <BulkRegeneratePriceDialog
          listings={pricePrompt}
          onClose={() => setPricePrompt(null)}
          onConfirm={(drops) => {
            const rows = pricePrompt;
            setPricePrompt(null);
            setReviewPrompt({ rows, drops });
          }}
        />
      ) : null}
      {markdownIds ? (
        <BulkPriceDropDialog
          conversationIds={markdownIds}
          onClose={() => {
            setMarkdownIds(null);
            exitSelecting();
          }}
        />
      ) : null}
      {reviewPrompt ? (
        <RegenerateReviewDialog
          listings={reviewPrompt.rows.map((row) => ({ id: row.id, title: row.title }))}
          onClose={() => setReviewPrompt(null)}
          onRewrite={() => {
            const { rows, drops } = reviewPrompt;
            setReviewPrompt(null);
            void finishRegenerate(rows.map((row) => row.id), drops);
          }}
        />
      ) : null}
    </>
  );
}

function bulkStatusLabel(
  run: BulkRegenerateRun | null,
  selectedCount: number,
  eligibleCount: number,
): string {
  if (run) {
    if (run.mode === "generate") {
      if (run.cancelRequested) return `Finishing ${run.active.size} active listings…`;
      return `Generating ${run.active.size} listings · ${run.finished} of ${run.total} finished`;
    }
    const { title } = run;
    const place = `${run.index + 1} of ${run.total}`;
    if (run.cancelRequested) return `Finishing ${title}…`;
    if (run.phase === "dropping") return `Dropping the price on ${title} (${place})`;
    if (run.phase === "finishing") return `Finishing fields on ${title} (${place})`;
    return `Rewriting ${title} (${place})`;
  }
  if (eligibleCount === 0) return "No listings with photos to rewrite";
  return `${selectedCount} selected`;
}
