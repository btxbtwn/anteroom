import { afterEach, describe, expect, it } from "vitest";
import type { MarketplaceListing } from "../api/marketplaceListings";
import {
  completeConfirmDialogClose,
  confirmDialog,
  readConfirmDialogState,
  registerConfirmDialogHost,
  respondToConfirmDialog,
} from "../ui/confirmDialog";
import { askDelistOnRegenerate } from "./ClearListingButton";

let unregister: (() => void) | null = null;
afterEach(() => {
  if (readConfirmDialogState().status === "confirming") respondToConfirmDialog(false);
  completeConfirmDialogClose();
  unregister?.();
  unregister = null;
});

const entry = (overrides: Partial<MarketplaceListing> = {}): MarketplaceListing => ({
  url: null, listing_id: "1", source: "anteroom", sold_here: false, state: "live", ...overrides,
});
const everywhere = () => true;

describe("Regenerate asks whether to delist the item too", () => {
  it("names each live marketplace and delists exactly those when the seller agrees", async () => {
    unregister = registerConfirmDialogHost();
    const answer = askDelistOnRegenerate("Wool coat", {
      ebay: entry(), poshmark: entry(), mercari: entry({ state: "deleted" }), depop: entry({ sold_here: true }),
    }, everywhere);
    await Promise.resolve();
    const state = readConfirmDialogState();
    expect(state.status).toBe("confirming");
    if (state.status !== "confirming") return;
    expect(state.message).toContain("Delist “Wool coat” from 2 marketplaces?");
    expect(state.message).toContain("• eBay: ended");
    expect(state.message).toContain("• Poshmark: deleted");
    expect(state.message).not.toContain("Mercari");
    expect(state.confirmLabel).toBe("Delist from 2");
    expect(state.cancelLabel).toBe("Keep them listed");
    respondToConfirmDialog(true);
    await expect(answer).resolves.toEqual(["ebay", "poshmark"]);
  });

  it("delists nothing when the seller keeps them listed", async () => {
    unregister = registerConfirmDialogHost();
    const answer = askDelistOnRegenerate("Wool coat", { ebay: entry() }, everywhere);
    await Promise.resolve();
    respondToConfirmDialog(false);
    await expect(answer).resolves.toEqual([]);
  });

  it("waits for the Rewrite prompt to finish closing before asking", async () => {
    unregister = registerConfirmDialogHost();
    const rewrite = confirmDialog("Rewrite this listing from scratch?", { confirmLabel: "Rewrite" });
    respondToConfirmDialog(true);
    await rewrite;
    const answer = askDelistOnRegenerate("Wool coat", { ebay: entry() }, everywhere);
    await Promise.resolve();
    expect(readConfirmDialogState().status).toBe("closing");
    completeConfirmDialogClose();
    await Promise.resolve();
    const state = readConfirmDialogState();
    expect(state.status === "confirming" && state.confirmLabel).toBe("Delist from eBay");
    respondToConfirmDialog(true);
    await expect(answer).resolves.toEqual(["ebay"]);
  });

  it("does not ask when nothing is live, or only where this app cannot act", async () => {
    unregister = registerConfirmDialogHost();
    await expect(askDelistOnRegenerate("Wool coat", { ebay: entry({ state: "deleted" }) }, everywhere)).resolves.toEqual([]);
    await expect(askDelistOnRegenerate("Wool coat", { etsy: entry() }, (marketplace) => marketplace !== "etsy")).resolves.toEqual([]);
    expect(readConfirmDialogState().status).not.toBe("confirming");
  });
});
