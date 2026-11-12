import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  followListingGeneration,
  runBulkRegenerate,
  type BulkPriceDrop,
  type BulkRegenerateProgress,
  type BulkRegenerateResult,
  type BulkRunMode,
} from "../bulkRegenerate";
import { refreshAfterReset } from "./ClearListingButton";
import {
  chatOwnsGeneration,
  clearChatGenerating,
  isChatResetting,
  markChatGenerating,
  markChatResetting,
  resetChatLive,
} from "./chat/liveChatStore";

export type BulkRegenerateRun = BulkRegenerateProgress & {
  cancelRequested: boolean;
  mode: BulkRunMode;
  /** The listing being worked on, as the seller named it. */
  title: string;
  active: ReadonlyMap<string, BulkRegenerateProgress>;
  finished: number;
};

export type BulkRegenerate = ReturnType<typeof useBulkRegenerate>;

type Batch = { cancelled: boolean; remaining: Set<string>; run: BulkRegenerateRun };

/**
 * Independent listings and batches generate concurrently.
 * Lives in App, so collapsing the sidebar does not drop a run. The open chat hears about the
 * run after the server has registered it, and attaches to that same stream.
 */
export function useBulkRegenerate(chatOpen: (id: string) => boolean) {
  const queryClient = useQueryClient();
  const chatOpenRef = useRef(chatOpen);
  useEffect(() => {
    chatOpenRef.current = chatOpen;
  }, [chatOpen]);
  const batches = useRef(new Set<Batch>());
  const [run, setRun] = useState<BulkRegenerateRun | null>(null);
  const [pendingIds, setPendingIds] = useState<string[]>([]);

  function refresh() {
    const current = [...batches.current];
    const latest = current[current.length - 1];
    setPendingIds([...new Set(current.flatMap((batch) => [...batch.remaining]))]);
    setRun(latest ? {
      ...latest.run,
      total: current.reduce((sum, batch) => sum + batch.run.total, 0),
      finished: current.reduce((sum, batch) => sum + batch.run.finished, 0),
      active: new Map(current.flatMap((batch) => [...batch.run.active])),
      cancelRequested: current.every((batch) => batch.cancelled),
    } : null);
  }

  async function start(
    ids: string[],
    titles: ReadonlyMap<string, string>,
    mode: BulkRunMode = "rewrite",
    drops?: ReadonlyMap<string, BulkPriceDrop>,
  ): Promise<BulkRegenerateResult | null> {
    const pending = new Set([...batches.current].flatMap((batch) => [...batch.remaining]));
    ids = [...new Set(ids)].filter((id) => !pending.has(id));
    if (ids.length === 0) return null;
    const marked = new Set<string>();
    const remaining = new Set(ids);
    const active = new Map<string, BulkRegenerateProgress>();
    const planned = mode === "rewrite" && drops && drops.size > 0 ? drops : undefined;
    const titleOf = (id: string) => titles.get(id) || "listing";
    const batch: Batch = { cancelled: false, remaining, run: {
      index: 0,
      total: ids.length,
      id: ids[0] || "",
      phase: planned?.has(ids[0] || "") ? "dropping" : "resetting",
      cancelRequested: false,
      mode,
      title: titleOf(ids[0] || ""),
      active: new Map(),
      finished: 0,
    } };
    batches.current.add(batch);
    refresh();
    try {
      return await runBulkRegenerate(ids, {
        willDrop: planned ? (id) => planned.has(id) : undefined,
        async drop(id) {
          const drop = planned?.get(id);
          if (!drop) return;
          await api.listings.applyPriceDrop(id, {
            price: drop.price,
            percent: drop.percent,
            mode: drop.mode,
          });
          void queryClient.invalidateQueries({ queryKey: ["listing", id] });
          void queryClient.invalidateQueries({ queryKey: ["conversations"] });
          void queryClient.invalidateQueries({ queryKey: ["price-drop-preview", id] });
        },
        async reset(id) {
          // A fresh draft has no chat or generated fields to wipe.
          if (mode === "generate") return;
          const showWipe = chatOpenRef.current(id);
          resetChatLive(id);
          if (showWipe) {
            markChatResetting(id, true);
            marked.add(id);
          }
          try {
            await api.conversations.reset(id, { keepInputs: true });
            await queryClient.cancelQueries({ queryKey: ["listing", id] });
            await queryClient.cancelQueries({ queryKey: ["messages", id] });
            // The reset is saved. Editor reads may stall independently, so
            // refresh the caches without blocking the confirmed generation.
            void refreshAfterReset(queryClient, id);
          } catch (err) {
            if (showWipe) markChatResetting(id, false);
            marked.delete(id);
            throw err;
          }
        },
        accepted(id) {
          return !marked.has(id) || isChatResetting(id);
        },
        async generate(id) {
          const generation = { started: false };
          try {
            await followListingGeneration(id, {
              onStarted: () => {
                generation.started = true;
                markChatGenerating(id);
              },
            });
          } finally {
            if (!generation.started) {
              // The wipe was already on screen and the rewrite never began.
              markChatResetting(id, false);
            } else if (!chatOwnsGeneration(id)) {
              clearChatGenerating(id);
            }
          }
        },
        async pending(id) {
          try {
            const activity = await api.conversations.activity(id);
            return activity.busy;
          } catch {
            return false;
          }
        },
        sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      }, {
        cancelled: () => batch.cancelled,
        onProgress: (progress) => {
          active.set(progress.id, progress);
          batch.run = {
            ...progress, cancelRequested: batch.cancelled, mode, title: titleOf(progress.id),
            active: new Map(active), finished: ids.length - remaining.size,
          };
          refresh();
        },
        onSettled: (id) => {
          remaining.delete(id);
          active.delete(id);
          batch.run = { ...batch.run, active: new Map(active), finished: ids.length - remaining.size };
          refresh();
        },
      });
    } finally {
      batches.current.delete(batch);
      refresh();
    }
  }

  function cancel() {
    for (const batch of batches.current) batch.cancelled = true;
    refresh();
  }

  return {
    running: run != null,
    run,
    pendingIds,
    start,
    cancel,
  };
}
