// @vitest-environment happy-dom
import { act, useLayoutEffect } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import type { BulkRegenerateResult } from "../../bulkRegenerate";
import type { BulkRegenerate } from "../useBulkRegenerate";
import { useListingSelection, type ListingSelection } from "./useListingSelection";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it.each([
  ["keeps a newer selection", (selection: ListingSelection) => selection.toggleSelect("hat", false, ["coat", "hat"]), ["hat"]],
  ["keeps a cleared selection", (selection: ListingSelection) => selection.exitSelecting(), []],
  ["restores failures when the selection is untouched", () => {}, ["coat"]],
] as const)("%s after an earlier rewrite fails", async (_, changeSelection, expected) => {
  const batch = Promise.withResolvers<BulkRegenerateResult>();
  const bulk: BulkRegenerate = { start: () => batch.promise, run: null, running: false, pendingIds: [], cancel: () => {} };
  const root = createRoot(document.createElement("div"));
  let current!: ListingSelection;

  function Harness() {
    const selection = useListingSelection({
      eligibleIds: ["coat", "hat"], listingsById: new Map(), suggestions: [], bulk,
    });
    useLayoutEffect(() => { current = selection; }, [selection]);
    return null;
  }

  try {
    act(() => root.render(<Harness />));
    let settled!: Promise<void>;
    act(() => { settled = current.finishRegenerate(["coat"]); });
    act(() => changeSelection(current));
    await act(async () => {
      batch.resolve({ completed: [], failed: [{ id: "coat", message: "Rewrite failed" }], skipped: [], cancelled: false });
      await settled;
    });
    expect([...current.selectedIds]).toEqual(expected);
  } finally {
    act(() => root.unmount());
  }
});
