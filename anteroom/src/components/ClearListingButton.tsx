import { useState } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DelistMarketplace, MarketplaceListings } from "../api/marketplaceListings";
import { useWorkspaceRuntime } from "../app/runtime";
import { confirmDialog, whenConfirmDialogIdle } from "../ui/confirmDialog";
import { toastManager } from "./ui/toast";
import {
  isChatResetting,
  markChatResetting,
  queueChatGenerate,
  resetChatLive,
  useChatBusy,
} from "./chat/liveChatStore";
import { RefreshCwIcon } from "lucide-react";
import { waitlist } from "./MarketplaceActivity";
import { delistPrompt, delistTargets } from "./marketplaceDelist";
import { RegeneratePriceDialog } from "./RegeneratePriceDialog";
import { Button } from "./ui/button";

const CLEAR_WARNING = [
  "Clear this listing and start over?",
  "Photos, chat, generated fields, and item details will be removed. This listing stays in the sidebar as a blank draft.",
].join("\n");

const REGENERATE_WARNING = [
  "Rewrite this listing from scratch?",
  "Chat and every generated field are discarded, then the listing is written again from your photos and item details. Measurements, flaws, COG, labels and notes are kept.",
].join("\n");

const REGENERATE_DELIST_LEAD = "The listing will be rewritten in Anteroom. Live listings keep the old copy unless you delist or update them.";

/** Items with a rewrite between its confirm and the end of its reset. Shared by every
    Regenerate button, since the editor rail and the empty chat can both show one for an item. */
const rewriting = new Set<string>();

/** Takes the item's rewrite hold; false when another rewrite of it already holds it. */
export function holdRewrite(convId: string): boolean {
  if (rewriting.has(convId)) return false;
  rewriting.add(convId);
  return true;
}

export function releaseRewrite(convId: string): void {
  rewriting.delete(convId);
}

/** After the seller confirms Regenerate: the marketplaces they also chose to delist from.
    Nothing live there means no question; "Keep them listed" means none. */
export async function askDelistOnRegenerate(
  title: string,
  listings: MarketplaceListings,
  canAct: (marketplace: string) => boolean,
): Promise<DelistMarketplace[]> {
  const targets = delistTargets(listings, canAct);
  if (!targets.length) return [];
  // The Rewrite prompt is still closing; a second click on it must not land on Delist.
  await whenConfirmDialogIdle();
  const { message, confirmLabel } = delistPrompt(title, targets, REGENERATE_DELIST_LEAD);
  const approved = await confirmDialog(message, { variant: "destructive", confirmLabel, cancelLabel: "Keep them listed" });
  return approved ? targets : [];
}

export async function refreshAfterReset(queryClient: QueryClient, convId: string) {
  queryClient.invalidateQueries({ queryKey: ["conversations"] });
  queryClient.invalidateQueries({ queryKey: ["jobs"] });
  queryClient.invalidateQueries({ queryKey: ["fill-log"] });
  queryClient.invalidateQueries({ queryKey: ["settings-hidden-fields"] });
  queryClient.invalidateQueries({ queryKey: ["listing-fields", convId] });
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["listing", convId] }),
    queryClient.invalidateQueries({ queryKey: ["conversation", convId] }),
    queryClient.invalidateQueries({ queryKey: ["photos", convId] }),
    queryClient.invalidateQueries({ queryKey: ["messages", convId] }),
  ]);
}

/** Clear lives in the titlebar's overflow menu, so the confirm dialog and the
    mutation must outlive the menu popup the click came from. */
export function useClearListing(convId: string, onCleared?: () => void) {
  const queryClient = useQueryClient();
  const reset = useMutation({
    mutationFn: () => api.conversations.reset(convId),
    onSuccess: () => {
      resetChatLive(convId);
      void refreshAfterReset(queryClient, convId);
      onCleared?.();
      toastManager.add({ type: "success", title: "Listing cleared", description: "Upload photos to start again." });
    },
    onError: (err: Error) => {
      toastManager.add({
        type: "error",
        title: "Could not clear listing",
        description: err.message || "The listing could not be cleared.",
      });
    },
  });

  return {
    isPending: reset.isPending,
    async clear() {
      const confirmed = await confirmDialog(CLEAR_WARNING, {
        variant: "destructive",
        confirmLabel: "Clear listing",
      });
      if (!confirmed) return;
      reset.mutate();
    },
  };
}

function listingHasPrice(listing: Record<string, unknown> | undefined): boolean {
  if (!listing) return false;
  const raw = listing.price;
  const amount = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(amount) && amount > 0;
}

/** When the listing has a price, opens the comps/percent drop chooser, which
 *  offers the rewrite itself. Otherwise a single confirm precedes the rewrite.
 *  Either way, an item still live somewhere is then asked whether to delist it too. */
export function RegenerateListingButton({
  convId,
  className,
  variant = "outline",
  onRegenerated,
}: {
  convId: string;
  className?: string;
  variant?: "outline" | "ghost-muted";
  onRegenerated?: () => void;
}) {
  const queryClient = useQueryClient();
  const runtime = useWorkspaceRuntime();
  const busy = useChatBusy(convId);
  const [priceDialogOpen, setPriceDialogOpen] = useState(false);
  // Held from the rewrite confirm through the delist question until the reset starts.
  const [preparing, setPreparing] = useState(false);

  const { data: photos } = useQuery({
    queryKey: ["photos", convId],
    queryFn: ({ signal }) => api.conversations.photos(convId, undefined, { signal }),
  });
  const { data: listingData } = useQuery({
    queryKey: ["listing", convId],
    queryFn: ({ signal }) => api.listings.get(convId, { signal }),
  });
  const hasPhotos = Boolean(photos?.length);
  const hasPrice = listingHasPrice(listingData?.listing);

  const regenerate = useMutation({
    mutationFn: async (delist: { title: string; marketplaces: DelistMarketplace[] }) => {
      resetChatLive(convId);
      // The wipe can take a moment; show it as a phase in the chat instead
      // of leaving an empty panel.
      markChatResetting(convId, true);
      await api.conversations.reset(convId, { keepInputs: true });
      // Queued only after the wipe, which cancels this item's marketplace jobs, and
      // before the reads below, so a failed read cannot drop them. The seller
      // approved exactly these marketplaces in the delist prompt.
      for (const marketplace of delist.marketplaces) {
        waitlist.add({ convId, title: delist.title, marketplace, kind: "delist", revisionId: "" });
      }
      if (delist.marketplaces.length) void queryClient.invalidateQueries({ queryKey: ["queue"] });
      // Chat must see the empty listing before it starts, or it reads the old
      // listing JSON as "generation already finished". Read both directly:
      // awaiting invalidateQueries also awaits any shared refetch, and one
      // whose retry React Query paused (window hidden) never settles.
      await queryClient.cancelQueries({ queryKey: ["listing", convId] });
      await queryClient.cancelQueries({ queryKey: ["messages", convId] });
      const [listing, messages] = await Promise.all([
        api.listings.get(convId),
        api.conversations.messages(convId),
      ]);
      queryClient.setQueryData(["listing", convId], listing);
      queryClient.setQueryData(["messages", convId], messages);
      void refreshAfterReset(queryClient, convId);
    },
    onSuccess: () => {
      // Stop during the wipe clears the phase; honour it instead of generating.
      if (!isChatResetting(convId)) return;
      // queueChatGenerate resets the live stream, which clears the wipe phase.
      queueChatGenerate(convId);
      onRegenerated?.();
    },
    onError: (err: Error) => {
      markChatResetting(convId, false);
      toastManager.add({
        type: "error",
        title: "Could not regenerate listing",
        description: err.message || "The listing could not be reset.",
      });
    },
  });

  /** Asks once, after the rewrite is confirmed, whether to delist the live listings too.
      Keeping them listed still rewrites. */
  async function rewriteAskingDelist() {
    // One rewrite per item: held from here until the reset settles, so a second
    // Rewrite while this one asks or resets is ignored.
    if (!holdRewrite(convId)) return;
    const hold = (held: boolean) => {
      if (!held) releaseRewrite(convId);
      setPreparing(held);
    };
    hold(true);
    try {
      regenerate.mutate(await prepareRewrite(), { onSettled: () => hold(false) });
    } catch (err) {
      hold(false);
      throw err;
    }
  }

  async function prepareRewrite(): Promise<{ title: string; marketplaces: DelistMarketplace[] }> {
    const title = String(listingData?.listing?.title || "this item");
    // Read fresh: a listing that went live, or was delisted, since the page loaded changes the question.
    const liveKey = ["marketplace-listings", convId];
    const listings = await queryClient
      .fetchQuery({ queryKey: liveKey, queryFn: ({ signal }) => api.marketplaceListings.get(convId, { signal }) })
      .catch(() => {
        const cached = queryClient.getQueryData<MarketplaceListings>(liveKey);
        if (!cached) {
          toastManager.add({
            type: "warning",
            title: "Could not check where this item is live",
            description: "The listing is still rewritten. Delist it under Marketplaces if it is still up.",
          });
        }
        return cached ?? {};
      });
    const marketplaces = await askDelistOnRegenerate(title, listings,
      (marketplace) => runtime.actionMarketplaces.has(marketplace));
    return { title, marketplaces };
  }

  async function confirmFullRegenerate() {
    const confirmed = await confirmDialog(REGENERATE_WARNING, {
      variant: "destructive",
      confirmLabel: "Rewrite",
    });
    if (!confirmed) return;
    await rewriteAskingDelist();
  }

  return (
    <>
      <Button
        variant={variant}
        size="xs"
        className={className}
        disabled={preparing || regenerate.isPending || busy || !hasPhotos}
        title={!hasPhotos
          ? "Add photos to generate a listing"
          : busy
            ? "Wait for the current generation to finish"
            : hasPrice
              ? "Drop the price, or rewrite the listing from scratch"
              : "Discard chat and generated fields, then write the listing again"}
        onClick={() => {
          if (hasPrice) {
            setPriceDialogOpen(true);
            return;
          }
          void confirmFullRegenerate();
        }}
      >
        <RefreshCwIcon />
        {regenerate.isPending ? "Resetting…" : "Regenerate…"}
      </Button>

      <RegeneratePriceDialog
        convId={convId}
        open={priceDialogOpen}
        onClose={() => setPriceDialogOpen(false)}
        onRewrite={() => {
          void rewriteAskingDelist();
        }}
      />
    </>
  );
}
