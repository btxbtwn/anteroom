import { BulkCopyDialog } from "../BulkCopyDialog";
import { BulkDeleteDialog } from "../BulkDeleteDialog";
import { BulkEditDialog } from "../BulkEditDialog";
import { BulkMarketplaceDialog } from "../BulkMarketplaceDialog";
import { PromotionDialog } from "../PromotionDialog";
import { BulkRegeneratePriceDialog } from "../BulkRegeneratePriceDialog";
import { BulkPriceDropDialog } from "../BulkPriceDropDialog";
import { RegenerateReviewDialog } from "../RegenerateReviewDialog";
import type { BulkRegenerate, BulkRegenerateRun } from "../useBulkRegenerate";
import type { ListingSelection } from "./useListingSelection";
import { useWorkspaceRuntime } from "../../app/runtime";
import { ChevronDownIcon } from "lucide-react";
import { Button } from "../ui/button";
import { Menu, MenuItem, MenuPopup, MenuSeparator, MenuTrigger } from "../ui/menu";

/** The bar under the list while selecting or while a bulk run is going.
 *  Like Polaris's bulk actions: the count leads, the common actions sit in
 *  the bar, the rest wait in More, and only Delete is styled destructive.
 *  Nothing acts until a listing is selected. */
export function BulkActionBar({
  bulk,
  selection,
  eligibleIds,
}: {
  bulk: BulkRegenerate;
  selection: ListingSelection;
  eligibleIds: string[];
}) {
  const { features } = useWorkspaceRuntime();
  const { selectedIds, toggleSelectAll, regenerateSelected, setMarkdownIds, setEditIds, setCopyIds, setMarketIds, setPromoteIds, setDeleteIds, exitSelecting } = selection;
  const count = selectedIds.size;
  const picked = () => eligibleIds.filter((id) => selectedIds.has(id));
  const none = count === 0;
  const allPicked = eligibleIds.length > 0 && eligibleIds.every((id) => selectedIds.has(id));
  const share = features.has("marketplace_actions");
  const regenerate = features.has("bulk_regenerate");
  const remove = features.has("bulk_delete");
  return (
    <div
      className="flex shrink-0 flex-col gap-2 border-t border-sidebar-border px-(--sidebar-content-inset) py-2 max-[900px]:sticky max-[900px]:bottom-0 max-[900px]:z-10 max-[900px]:bg-sidebar max-[900px]:pb-[max(8px,env(safe-area-inset-bottom,0px))]"
      data-bulk-bar
      role="region"
      aria-label="Bulk listing actions"
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <p role="status" className="min-w-0 flex-1 px-1 text-xs font-medium leading-snug text-sidebar-foreground tabular-nums">
          {bulkStatusLabel(bulk.run, count, eligibleIds.length)}
        </p>
        <Button type="button" variant="ghost" size="xs" disabled={eligibleIds.length === 0} onClick={toggleSelectAll}>
          {allPicked ? "Select none" : "Select all"}
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
          {bulk.running ? (bulk.run?.cancelRequested ? "Finishing…" : "Stop batch") : "Done"}
        </Button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {features.has("marketplace_actions") && (<Button type="button" variant="outline" size="xs" disabled={none || count > 100}
          title="Review List, Delist, Relist, Update or Mark as not listed on named marketplaces for up to 100 listings" onClick={() => setMarketIds(picked())}>Marketplaces</Button>)}
        {features.has("bulk_edit") && (<Button type="button" variant="outline" size="xs" disabled={none || count > 300}
          title="Review local field and label changes for up to 300 listings" onClick={() => setEditIds(picked())}>Edit fields</Button>)}
        {features.has("bulk_markdowns") && (<Button type="button" variant="outline" size="xs" disabled={none}
          title="Review a lower price for each selected listing. Saves in Anteroom only." onClick={() => setMarkdownIds(picked())}>Mark down</Button>)}
        {features.has("bulk_copy") && (<Button type="button" variant="outline" size="xs" disabled={none || count > 300}
          title="Review separate draft copies of up to 300 selected items" onClick={() => setCopyIds(picked())}>Copy items</Button>)}
        {(share || regenerate || remove) && (
          <Menu>
            <MenuTrigger disabled={none} render={<Button type="button" variant="outline" size="xs" />}>
              More<ChevronDownIcon aria-hidden />
            </MenuTrigger>
            <MenuPopup align="start" side="top" width="content">
              {share && <MenuItem disabled={count > 100} onClick={() => setPromoteIds(picked())}>Share / offers</MenuItem>}
              {regenerate && <MenuItem disabled={selection.regenerateIds.length === 0} onClick={() => { void regenerateSelected(); }}>Regenerate…</MenuItem>}
              {remove && (share || regenerate) && <MenuSeparator />}
              {remove && <MenuItem variant="destructive" disabled={count > 300} onClick={() => setDeleteIds(picked())}>Delete…</MenuItem>}
            </MenuPopup>
          </Menu>
        )}
      </div>
    </div>
  );
}

/** The price, Mark down and review dialogs a bulk action opens. */
export function BulkSelectionDialogs({ selection, onOpenListing }: { selection: ListingSelection; onOpenListing: (id: string) => void }) {
  const {
    editIds,
    setEditIds,
    copyIds,
    setCopyIds,
    marketIds,
    setMarketIds,
    promoteIds,
    setPromoteIds,
    deleteIds,
    setDeleteIds,
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
      {copyIds ? <BulkCopyDialog conversationIds={copyIds} onOpenListing={(id) => { setCopyIds(null); exitSelecting(); onOpenListing(id); }} onClose={() => { setCopyIds(null); exitSelecting(); }} /> : null}
      {marketIds ? <BulkMarketplaceDialog conversationIds={marketIds} onClose={() => { setMarketIds(null); exitSelecting(); }} /> : null}
      {promoteIds ? <PromotionDialog conversationIds={promoteIds} onClose={() => { setPromoteIds(null); exitSelecting(); }} /> : null}
      {deleteIds ? <BulkDeleteDialog conversationIds={deleteIds} onClose={() => { setDeleteIds(null); exitSelecting(); }} /> : null}
      {editIds ? <BulkEditDialog conversationIds={editIds} onClose={() => { setEditIds(null); exitSelecting(); }} /> : null}
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
  if (eligibleCount === 0) return "No listings to select";
  return `${selectedCount} selected`;
}
