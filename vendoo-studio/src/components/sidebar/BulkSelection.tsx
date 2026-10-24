import { BulkRegeneratePriceDialog } from "../BulkRegeneratePriceDialog";
import { BulkPriceDropDialog } from "../BulkPriceDropDialog";
import { RegenerateReviewDialog } from "../RegenerateReviewDialog";
import type { BulkRegenerate, BulkRegenerateRun } from "../useBulkRegenerate";
import type { ListingSelection } from "./useListingSelection";
import { Button } from "../ui/button";

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
    <div
      className="flex shrink-0 flex-col gap-1.5 border-t border-sidebar-border px-(--sidebar-content-inset) py-2 max-[900px]:sticky max-[900px]:bottom-0 max-[900px]:z-10 max-[900px]:bg-sidebar max-[900px]:pb-[max(8px,env(safe-area-inset-bottom,0px))]"
      data-bulk-bar
      role="region"
      aria-label="Regenerate listings"
    >
      <p className="min-w-0 px-1 text-xs font-medium leading-snug text-sidebar-foreground">
        {bulkStatusLabel(bulk.run, selectedIds.size, eligibleIds.length)}
      </p>
      <div className="flex flex-wrap gap-1.5">
        <Button
          type="button"
          variant="ghost"
          size="xs"
          disabled={bulk.running || eligibleIds.length === 0}
          onClick={toggleSelectAll}
        >
          {eligibleIds.length > 0 && eligibleIds.every((id) => selectedIds.has(id)) ? "None" : "All"}
        </Button>
        <Button
          type="button"
          variant="destructive"
          size="xs"
          disabled={bulk.running || selectedIds.size === 0}
          onClick={() => { void regenerateSelected(); }}
        >
          Regenerate
        </Button>
        <Button
          type="button"
          variant="outline"
          size="xs"
          title="Review a lower price for each selected listing. Saves in Anteroom only; nothing is sent to Vendoo."
          disabled={bulk.running || selectedIds.size === 0}
          onClick={() => setMarkdownIds(eligibleIds.filter((id) => selectedIds.has(id)))}
        >
          Mark down
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="xs"
          disabled={Boolean(bulk.run?.cancelRequested)}
          onClick={() => {
            if (bulk.running) bulk.cancel();
            else exitSelecting();
          }}
        >
          {bulk.running ? (bulk.run?.cancelRequested ? "Finishing…" : "Stop batch") : "Cancel"}
        </Button>
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
