/** Starring an item: the seller's own mark for the items to find again. */
import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { StarIcon } from "lucide-react";
import { api } from "../api/client";
import { refreshInventory } from "../api/inventoryRefresh";
import type { Conversation, ConversationSummary } from "../api/types";
import { cn } from "../lib/utils";
import { toastManager } from "./ui/toast";
import { Toggle } from "./ui/toggle";

type Change = { id: string; favorite: boolean };

/** The row with its star changed, leaving every other row as it was. */
export function withFavorite<T extends { id: string; favorite?: boolean }>(rows: T[] | undefined, { id, favorite }: Change): T[] | undefined {
  return rows?.map((row) => (row.id === id ? { ...row, favorite } : row));
}

function applyFavorite(client: QueryClient, change: Change) {
  client.setQueryData<ConversationSummary[]>(["conversations"], (rows) => withFavorite(rows, change));
  client.setQueryData<Conversation>(["conversation", change.id], (row) => (row ? { ...row, favorite: change.favorite } : row));
}

const writes = new Map<string, { tail: Promise<unknown>; pending: number }>();

/**
 * Sends one item's star writes one after another, so the server keeps the
 * seller's last choice. Resolves `true` when this was the item's latest write,
 * `false` when a newer one is still waiting and decides the star; only the
 * latest write's failure is thrown.
 */
export async function queueFavoriteWrite(id: string, write: () => Promise<unknown>): Promise<boolean> {
  const entry = writes.get(id) ?? { tail: Promise.resolve(), pending: 0 };
  writes.set(id, entry);
  const sent = entry.tail.catch(() => undefined).then(write);
  entry.tail = sent;
  entry.pending += 1;
  let failure: { error: unknown } | undefined;
  try {
    await sent;
  } catch (error) {
    failure = { error };
  }
  entry.pending -= 1;
  if (entry.pending > 0) return false;
  writes.delete(id);
  if (failure) throw failure.error;
  return true;
}

/**
 * Stars or unstars an item. The star changes on screen at once and goes back
 * if the server refuses it; starring is not an edit, so nothing else moves.
 */
export function useSetFavorite() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, favorite }: Change) => queueFavoriteWrite(id, () => api.conversations.setFavorite(id, favorite)),
    onMutate: async (change: Change) => {
      // A read already on its way would land with the old star; the star wins, then the list rereads.
      await Promise.all([
        client.cancelQueries({ queryKey: ["conversations"], exact: true }),
        client.cancelQueries({ queryKey: ["conversation", change.id], exact: true }),
      ]);
      applyFavorite(client, change);
    },
    onSettled: (latest: boolean | undefined, _error, change: Change) => {
      // A newer star for this item is still on its way; its own reread follows it.
      if (latest === false) return;
      refreshInventory(client);
      // Starring may have cancelled the item's first read; read it again so the editor is not left empty.
      void client.invalidateQueries({ queryKey: ["conversation", change.id], exact: true });
    },
    onError: (error: Error, change: Change) => {
      applyFavorite(client, { id: change.id, favorite: !change.favorite });
      toastManager.add({
        type: "error",
        title: change.favorite ? "Could not add to favorites" : "Could not remove from favorites",
        description: error.message || "Check your connection and try again.",
      });
    },
  });
}

/** A star that reads as filled while on; on its own it says nothing, so it carries a name. */
export function FavoriteStar({ on, className }: { on: boolean; className?: string }) {
  return <StarIcon aria-hidden="true" className={cn(on && "fill-current", className)} />;
}

/** The star button for one item, as Gmail and Photos offer it: pressed while the item is a favorite. */
export function FavoriteToggle({ id, title, favorite, className }: {
  id: string;
  title: string;
  favorite: boolean;
  className?: string;
}) {
  const setFavorite = useSetFavorite();
  const label = favorite ? "Remove from favorites" : "Add to favorites";
  return (
    <Toggle
      size="xs"
      variant="ghost"
      pressed={favorite}
      aria-label={`${label}: ${title}`}
      title={label}
      className={cn("text-muted-foreground data-pressed:bg-transparent data-pressed:text-foreground", className)}
      onPressedChange={(pressed) => setFavorite.mutate({ id, favorite: pressed })}
    >
      <FavoriteStar on={favorite} />
    </Toggle>
  );
}
