import React from "react";
import { type QueryClient } from "@tanstack/react-query";
import { type SseParts } from "../sseCoalesce";
import { stableStreamingText } from "../streamingText";
import { settleAgents, type LiveAgents, type LiveTraceEntry } from "../liveTrace";
import { turnHasWork, type FoldedTurn } from "./workLogModel";
import { isStreamError } from "./assistantDisplay";
import { TRACE_ONLY_EVENTS } from "./chatStream";

type LiveStream = {
  controller: AbortController | null;
  streaming: boolean;
  generating: boolean;
  streamText: string;
  streamDisplayText: string;
  /** What the run has done so far, shown inline in the transcript. */
  streamTrace: LiveTraceEntry[];
  streamStatus: string;
  thinkingStarted: boolean;
  failedAction: "generate" | "send" | null;
  lastSendText: string;
  userCancelled: boolean;
  restoreInputOnAbort: boolean;
  /** Set by queueChatGenerate: the next attach starts a run instead of resuming one. */
  startQueued: boolean;
  /** Regenerate's wipe is in flight; the chat is empty and nothing streams yet. */
  resetting: boolean;
  /** Wall-clock start of the current busy stretch (Working for …). */
  workStartedAtMs: number | null;
  /** Start and end of the latest busy stretch; outlive it so the fold can say how long it took. */
  lastWorkStartedAtMs: number | null;
  lastWorkEndedAtMs: number | null;
  /** Subagents the current turn kicked off, by id. Cleared with the trace. */
  streamAgents: LiveAgents;
  /** Message ids on screen when the current turn started. */
  turnBaseIds: string[];
  /** Finished turns of this session, each folded into a "Worked for" row. */
  foldedTurns: FoldedTurn[];
  listeners: Set<() => void>;
};

const liveStreams: Record<string, LiveStream> = {};

function emptyLive(): Omit<LiveStream, "listeners"> {
  return {
    controller: null,
    streaming: false,
    generating: false,
    streamText: "",
    streamDisplayText: "",
    streamTrace: [],
    streamStatus: "",
    thinkingStarted: false,
    failedAction: null,
    lastSendText: "",
    userCancelled: false,
    restoreInputOnAbort: false,
    startQueued: false,
    resetting: false,
    workStartedAtMs: null,
    lastWorkStartedAtMs: null,
    lastWorkEndedAtMs: null,
    streamAgents: {},
    turnBaseIds: [],
    foldedTurns: [],
  };
}

let foldSeq = 0;

/**
 * Fold the finished turn's work into a "Worked for" row, T3 Code style, and
 * clear the live trace. Kept for the session only. A turn with no work to show
 * folds into nothing.
 */
export function foldLiveTurn(convId: string, { cancelled = false }: { cancelled?: boolean } = {}) {
  const live = getLive(convId);
  const trace = live.streamTrace;
  const endedAtMs = live.lastWorkEndedAtMs ?? Date.now();
  if (turnHasWork(trace)) {
    foldSeq += 1;
    const turn: FoldedTurn = {
      id: `turn-${foldSeq}`,
      trace,
      agents: settleAgents(live.streamAgents, endedAtMs),
      startedAtMs: live.workStartedAtMs ?? live.lastWorkStartedAtMs ?? endedAtMs,
      endedAtMs: live.workStartedAtMs != null ? Date.now() : endedAtMs,
      cancelled,
      baseIds: live.turnBaseIds,
    };
    live.foldedTurns = [...live.foldedTurns, turn];
  }
  live.streamTrace = [];
  live.streamAgents = {};
  emitLive(convId);
}

export function getLive(convId: string): LiveStream {
  if (!liveStreams[convId]) liveStreams[convId] = { ...emptyLive(), listeners: new Set() };
  return liveStreams[convId];
}

function emitLive(convId: string) {
  getLive(convId).listeners.forEach((listener) => listener());
}

export function resetChatLive(convId: string) {
  const live = liveStreams[convId];
  if (!live) return;
  const controller = live.controller;
  live.restoreInputOnAbort = false;
  controller?.abort();
  Object.assign(live, emptyLive());
  emitLive(convId);
}

/** Show the wipe as a phase of its own, so Regenerate is not a blank chat.
 *  Cleared by queueChatGenerate, which hands the chat over to the stream. */
export function markChatResetting(convId: string, resetting: boolean) {
  patchLive(convId, { resetting });
}

/** False once Stop has dropped the wipe phase, so the caller skips the generate. */
export function isChatResetting(convId: string): boolean {
  return getLive(convId).resetting;
}

/** The open chat is attached to this run, so bulk regenerate must leave it alone. */
export function chatOwnsGeneration(convId: string): boolean {
  const live = getLive(convId);
  return Boolean(live.controller) || live.streaming;
}

/**
 * A background rewrite has started. The open chat attaches with resume
 * (`startQueued` stays false) instead of posting a second generate.
 */
export function markChatGenerating(convId: string) {
  if (chatOwnsGeneration(convId)) return;
  patchLive(convId, {
    generating: true,
    startQueued: false,
    resetting: false,
    userCancelled: false,
    streamStatus: getLive(convId).streamStatus || "Analyzing photos…",
  });
}

/** Drop a background generating flag the chat never took over. */
export function clearChatGenerating(convId: string) {
  if (chatOwnsGeneration(convId)) return;
  patchLive(convId, {
    generating: false,
    streaming: false,
    startQueued: false,
    resetting: false,
    streamStatus: "",
    streamText: "",
    streamDisplayText: "",
    streamTrace: [],
    thinkingStarted: false,
  });
}

/** Start a generate for this listing; a mounted ChatPanel picks it up and streams it. */
export function queueChatGenerate(convId: string) {
  resetChatLive(convId);
  patchLive(convId, { generating: true, streamStatus: "Analyzing photos…", startQueued: true });
}

/** True while the listing is being generated or chat is still answering. */
export function useChatBusy(convId: string): boolean {
  return React.useSyncExternalStore(
    (listener) => {
      const live = getLive(convId);
      live.listeners.add(listener);
      return () => live.listeners.delete(listener);
    },
    () => {
      const live = getLive(convId);
      return live.streaming || live.generating || live.resetting;
    },
  );
}

export function patchLive(convId: string, patch: Partial<Omit<LiveStream, "listeners">>) {
  const live = getLive(convId);
  const wasBusy = live.streaming || live.generating || live.resetting;
  Object.assign(live, patch);
  const busy = live.streaming || live.generating || live.resetting;
  if (patch.streamText !== undefined && patch.streamDisplayText === undefined) {
    live.streamDisplayText = isStreamError(patch.streamText)
      ? patch.streamText
      : busy ? stableStreamingText(patch.streamText) : patch.streamText;
  } else if (wasBusy && !busy && live.streamText) {
    // Completion flushes the active paragraph, just as T3 Code flushes its
    // paragraph buffer when the provider ends the turn.
    live.streamDisplayText = live.streamText;
  }
  // Whoever clears the trace means the whole turn's work, agents included.
  if (patch.streamTrace?.length === 0 && patch.streamAgents === undefined) live.streamAgents = {};
  if (busy && live.workStartedAtMs == null) {
    live.workStartedAtMs = Date.now();
    live.lastWorkStartedAtMs = live.workStartedAtMs;
    live.lastWorkEndedAtMs = null;
  }
  if (!busy && wasBusy) live.lastWorkEndedAtMs = Date.now();
  if (!busy) live.workStartedAtMs = null;
  emitLive(convId);
}

export function applySseToLive(convId: string, event: string, parts: SseParts) {
  const live = getLive(convId);
  const previousDisplayText = live.streamDisplayText;
  const traceChanged = parts.trace !== live.streamTrace || parts.agents !== live.streamAgents;
  live.streamTrace = parts.trace;
  live.streamAgents = parts.agents;
  if (event === "thinking" || event === "status") live.thinkingStarted = true;
  if (event === "status") live.streamStatus = parts.status;
  if (event !== "thinking" && event !== "status" && event !== "listing_updated" && !TRACE_ONLY_EVENTS.has(event)) {
    live.streamText = parts.content;
    live.streamDisplayText = stableStreamingText(parts.content);
    if (live.streamDisplayText === previousDisplayText && !traceChanged) return;
  }
  emitLive(convId);
}

export async function refreshListingQueries(queryClient: QueryClient, convId: string) {
  await queryClient.invalidateQueries({ queryKey: ["messages", convId] });
  await queryClient.invalidateQueries({ queryKey: ["listing", convId] });
  await queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
  queryClient.invalidateQueries({ queryKey: ["conversations"] });
  queryClient.invalidateQueries({ queryKey: ["fill-log"] });
  queryClient.invalidateQueries({ queryKey: ["activity", convId] });
}
