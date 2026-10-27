import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  bulkRegenerateToast,
  listingCanRegenerate,
  toggleListingSelection,
  type BulkPriceDrop,
  type BulkRegenerateListing,
} from "../../bulkRegenerate";
import { isConfirmDialogOpen } from "../../ui/confirmDialog";
import { toastManager } from "../ui/toast";
import type { Suggestion } from "../../api/types";
import type { BulkRegenerate } from "../useBulkRegenerate";
import type { Listing } from "./listingShelves";

export type ListingSelection = ReturnType<typeof useListingSelection>;

/**
 * Select mode in the sidebar: which listings are picked for a bulk Regenerate
 * or Mark down, and the price and review prompts that run before a rewrite.
 */
export function useListingSelection({
  eligibleIds,
  listingsById,
  suggestions,
  bulk,
}: {
  eligibleIds: string[];
  listingsById: ReadonlyMap<string, Listing>;
  suggestions: Suggestion[];
  bulk: BulkRegenerate;
}) {
  const [selecting, setSelecting] = useState(false);
  const [pricePrompt, setPricePrompt] = useState<BulkRegenerateListing[] | null>(null);
  // Photos, labels and measurements are checked before anything is wiped.
  const [reviewPrompt, setReviewPrompt] = useState<
    { rows: BulkRegenerateListing[]; drops?: ReadonlyMap<string, BulkPriceDrop> } | null
  >(null);
  const [pickedIds, setPickedIds] = useState<Set<string>>(() => new Set());
  const [markdownIds, setMarkdownIds] = useState<string[] | null>(null);
  const [copyIds, setCopyIds] = useState<string[] | null>(null);
  const [editIds, setEditIds] = useState<string[] | null>(null);
  const [marketIds, setMarketIds] = useState<string[] | null>(null);
  const [promoteIds, setPromoteIds] = useState<string[] | null>(null);
  const selectionAnchor = useRef<string | null>(null);

  const selectedIds = useMemo(() => {
    if (pickedIds.size === 0) return pickedIds;
    const eligible = new Set(eligibleIds);
    let changed = false;
    const next = new Set<string>();
    for (const id of pickedIds) {
      if (eligible.has(id)) next.add(id);
      else changed = true;
    }
    return changed ? next : pickedIds;
  }, [eligibleIds, pickedIds]);

  useEffect(() => {
    if (!selecting || bulk.running) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape" || isConfirmDialogOpen() || pricePrompt || reviewPrompt || markdownIds || editIds || copyIds || marketIds || promoteIds) return;
      setSelecting(false);
      setPickedIds(new Set());
      selectionAnchor.current = null;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selecting, bulk.running, pricePrompt, reviewPrompt, markdownIds, editIds, copyIds, marketIds, promoteIds]);

  const exitSelecting = useCallback(() => {
    setSelecting(false);
    setPickedIds(new Set());
    selectionAnchor.current = null;
  }, []);

  const toggleSelect = useCallback((id: string, shift: boolean, orderedIds: readonly string[]) => {
    setPickedIds((prev) => {
      const eligible = new Set(eligibleIds);
      const visible = new Set<string>();
      for (const picked of prev) {
        if (eligible.has(picked)) visible.add(picked);
      }
      const next = toggleListingSelection(visible, id, orderedIds, shift, selectionAnchor.current);
      selectionAnchor.current = next.anchorId;
      return next.selected;
    });
  }, [eligibleIds]);

  const toggleSelectAll = useCallback(() => {
    setPickedIds((prev) => {
      const allSelected = eligibleIds.length > 0 && eligibleIds.every((id) => prev.has(id));
      selectionAnchor.current = null;
      return allSelected ? new Set() : new Set(eligibleIds);
    });
  }, [eligibleIds]);

  const finishRegenerate = useCallback(async (
    ids: string[],
    drops?: ReadonlyMap<string, BulkPriceDrop>,
  ) => {
    const titles = new Map(ids.map((id) => {
      const listing = listingsById.get(id);
      const suggestion = suggestions.find((item) => item.conversation_id === id);
      return [id, listing?.title || suggestion?.title || "Untitled"];
    }));
    // The bulk bar tracks the run on its own; checkboxes would only sit locked.
    exitSelecting();
    const result = await bulk.start(ids, titles, "rewrite", drops);
    if (!result) return;
    if (result.failed.length > 0) {
      setPickedIds(new Set(result.failed.map((item) => item.id)));
      setSelecting(true);
    }
    toastManager.add(bulkRegenerateToast(result));
  }, [bulk, exitSelecting, listingsById, suggestions]);

  const regenerateIds = useMemo(() => eligibleIds.filter((id) => selectedIds.has(id) && (
    listingsById.has(id) ? listingCanRegenerate(listingsById.get(id)!) : Boolean(suggestions.find((row) => row.conversation_id === id)?.cover_photo_url)
  )), [eligibleIds, listingsById, selectedIds, suggestions]);

  const regenerateSelected = useCallback(async () => {
    const ids = regenerateIds;
    if (!ids.length || bulk.running) return;
    const rows: BulkRegenerateListing[] = ids.map((id) => {
      const listing = listingsById.get(id);
      const suggestion = suggestions.find((item) => item.conversation_id === id);
      const price = listing?.price;
      return {
        id,
        title: listing?.title || suggestion?.title || "Untitled",
        price: price != null && price > 0 ? price : null,
      };
    });
    if (!rows.some((row) => row.price != null)) {
      setReviewPrompt({ rows });
      return;
    }
    setPricePrompt(rows);
  }, [bulk.running, regenerateIds, listingsById, suggestions]);

  return {
    selecting,
    setSelecting,
    selectedIds,
    exitSelecting,
    toggleSelect,
    toggleSelectAll,
    regenerateSelected,
    regenerateIds,
    finishRegenerate,
    pricePrompt,
    setPricePrompt,
    reviewPrompt,
    setReviewPrompt,
    markdownIds,
    setMarkdownIds,
    editIds,
    setEditIds,
    copyIds,
    setCopyIds,
    marketIds,
    setMarketIds,
    promoteIds,
    setPromoteIds,
  };
}
