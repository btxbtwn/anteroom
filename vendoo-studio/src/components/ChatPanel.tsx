import React, { useState, useRef, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BrowserField } from "../api/client";
import { activityPollMs, BUSY_POLL_MS, LIVE_POLL_MS, pollMs } from "../api/polling";
import type { ConversationActivity, Job, Message } from "../api/types";
import { ChatMarkdown } from "./ChatMarkdown";
import {
  enqueueChatMessage,
  newQueuedChatMessage,
  removeChatMessage,
  takeNextChatMessage,
  type QueuedChatMessage,
} from "./chatMessageQueue";
import { CiteSelectionToolbar } from "./CiteSelectionToolbar";
import { type ChatCitation } from "./chatCitations";
import { revealChatCitation } from "./chatCitationDom";
import { ChatCitationRevealContext } from "./chatCitationContext";
import { ComposerPromptEditor, type ComposerPromptEditorHandle } from "./ComposerPromptEditor";
import { SendProgress } from "./SendProgress";
import { EvidenceCard } from "./EvidenceCard";
import { SoldCompsCard } from "./SoldCompsCard";
import { WorkingDuration } from "./WorkingDuration";
import {
  currentPhaseIndex,
  settledPhaseText,
  type LiveTraceEntry,
} from "./liveTrace";
import { LiveActivityRow, LiveThought, PhaseIcon, SearchIcon } from "./chat/LiveActivity";
import {
  assistantDisplayText,
  extractJson,
  isCompResearch,
  isListingJson,
  isPhotoAnalysis,
  isStreamError,
} from "./chat/assistantDisplay";
import {
  consumeResponseSse,
  detailFromResponseBody,
  formatClientStreamError,
  isAbortError,
  isResumableGenerateFailure,
  sleep,
  SSE_FETCH,
  type StreamRequestError,
} from "./chat/chatStream";
import { applySseToLive, getLive, patchLive, refreshListingQueries } from "./chat/liveChatStore";

interface Props {
  convId: string;
  queuedMessage?: string | null;
  onQueuedMessageConsumed?: () => void;
  /** Draft open in Studio's browser. Messages then go to the fix agent with the picked fields. */
  browser?: { jobId: string; fields: BrowserField[] } | null;
  onBrowserFieldsChange?: (fields: BrowserField[]) => void;
}

const MARKET_LABELS: Record<string, string> = {
  general: "Vendoo",
  ebay: "eBay",
  etsy: "Etsy",
  poshmark: "Poshmark",
  mercari: "Mercari",
  depop: "Depop",
};


export function ChatPanel({ convId, queuedMessage, onQueuedMessageConsumed, browser, onBrowserFieldsChange }: Props) {
  const browserRef = useRef(browser);
  browserRef.current = browser;
  const live = getLive(convId);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(live.streaming);
  const [generating, setGenerating] = useState(live.generating);
  const [streamText, setStreamText] = useState(live.streamDisplayText);
  const [streamTrace, setStreamTrace] = useState(live.streamTrace);
  const [streamStatus, setStreamStatus] = useState(live.streamStatus);
  const [resetting, setResetting] = useState(live.resetting);
  const [workStartedAtMs, setWorkStartedAtMs] = useState(live.workStartedAtMs);
  const [failedAction, setFailedAction] = useState<"generate" | "send" | null>(live.failedAction);
  const [lastSendText, setLastSendText] = useState(live.lastSendText);
  const [stopping, setStopping] = useState(false);
  // Follow-ups typed while a run is still writing — same idea as T3 Code's
  // composer queue: hold them client-side and send when the turn settles.
  const [pendingQueue, setPendingQueue] = useState<QueuedChatMessage[]>([]);
  // Quotes live inline in the composer itself, T3 Code style: `input` is the
  // markdown prompt, and a quote inside it is its citation link.
  const composerRef = useRef<ComposerPromptEditorHandle | null>(null);
  const drainLockRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [chatViewport, setChatViewport] = useState<HTMLDivElement | null>(null);
  const setScrollEl = useCallback((el: HTMLDivElement | null) => {
    scrollRef.current = el;
    setChatViewport(el);
  }, []);
  const stickToBottomRef = useRef(true);
  const queryClient = useQueryClient();

  useEffect(() => {
    setPendingQueue([]);
    drainLockRef.current = false;
  }, [convId]);

  const isNearBottom = (el: HTMLDivElement, threshold = 96) =>
    el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;

  const scrollChatToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo(0, el.scrollHeight);
  }, []);

  const pinChatToBottom = useCallback(() => {
    stickToBottomRef.current = true;
    scrollChatToBottom();
  }, [scrollChatToBottom]);

  // The server's view of the listing: background field fills, saves, syncs and
  // Vendoo jobs keep running after a chat stream ends and can still post here.
  const { data: activity } = useQuery({
    queryKey: ["activity", convId],
    queryFn: () => api.conversations.activity(convId),
    refetchInterval: (query) =>
      activityPollMs(getLive(convId).streaming || getLive(convId).generating || Boolean(query.state.data?.busy)),
  });
  const serverBusy = Boolean(activity?.busy);
  const busy = streaming || generating || serverBusy || resetting;

  const { data: messages, isLoading } = useQuery({
    queryKey: ["messages", convId],
    queryFn: () => api.conversations.messages(convId),
    refetchInterval: busy ? () => pollMs(LIVE_POLL_MS) : false,
  });

  const { data: photos } = useQuery({
    queryKey: ["photos", convId],
    queryFn: () => api.conversations.photos(convId),
  });

  const { data: listing } = useQuery({
    queryKey: ["listing", convId],
    queryFn: () => api.listings.get(convId),
    refetchInterval: busy ? () => pollMs(BUSY_POLL_MS) : false,
  });

  const { data: jobs } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.jobs.list(),
    refetchInterval: busy ? () => pollMs(BUSY_POLL_MS) : false,
  });

  // Anything posted in the background shows up as soon as activity notices it.
  const lastMessageId = messages?.length ? messages[messages.length - 1].id : null;
  useEffect(() => {
    if (!activity || !messages) return;
    if (activity.message_count === messages.length && activity.last_message_id === lastMessageId) return;
    void queryClient.invalidateQueries({ queryKey: ["messages", convId] });
  }, [activity, messages, lastMessageId, convId, queryClient]);

  // Background work that finishes on its own may have changed the listing too.
  const wasServerBusyRef = useRef(serverBusy);
  useEffect(() => {
    if (wasServerBusyRef.current && !serverBusy) void refreshListingQueries(queryClient, convId);
    wasServerBusyRef.current = serverBusy;
  }, [serverBusy, convId, queryClient]);

  const activeProbe = (jobs || []).find(
    (j) =>
      j.conversation_id === convId
      && j.mode === "schema_probe"
      && ["queued", "awaiting_extension", "dispatched"].includes(String(j.status || "")),
  );

  useEffect(() => {
    const sync = () => {
      const next = getLive(convId);
      setStreaming(next.streaming);
      setGenerating(next.generating);
      setStreamText(next.streamDisplayText);
      setStreamTrace(next.streamTrace);
      setStreamStatus(next.streamStatus);
      setResetting(next.resetting);
      setWorkStartedAtMs(next.workStartedAtMs);
      setFailedAction(next.failedAction);
      setLastSendText(next.lastSendText);
    };
    setInput("");
    sync();
    getLive(convId).listeners.add(sync);
    return () => {
      getLive(convId).listeners.delete(sync);
    };
  }, [convId]);

  useEffect(() => {
    stickToBottomRef.current = true;
    scrollChatToBottom();
  }, [convId, scrollChatToBottom]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickToBottomRef.current) return;
    if (!isNearBottom(el)) {
      stickToBottomRef.current = false;
      return;
    }
    scrollChatToBottom();
  }, [messages, streamText, streamTrace, scrollChatToBottom]);

  const handleChatScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = isNearBottom(el);
  }, []);

  const handleChatWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    if (e.deltaY < 0) stickToBottomRef.current = false;
  }, []);

  const touchYRef = useRef<number | null>(null);
  const handleChatTouchStart = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    touchYRef.current = e.touches[0]?.clientY ?? null;
  }, []);
  const handleChatTouchMove = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    const y = e.touches[0]?.clientY;
    if (touchYRef.current == null || y == null) return;
    if (y > touchYRef.current + 2) stickToBottomRef.current = false;
    touchYRef.current = y;
  }, []);

  const streamFromFetch = useCallback(async (url: string, initialStatus = "", resumeFirst = false) => {
    const liveState = getLive(convId);
    liveState.controller?.abort();
    const controller = new AbortController();
    patchLive(convId, {
      controller,
      streaming: true,
      generating: url.endsWith("/generate") ? true : liveState.generating,
      streamText: "",
      streamTrace: [],
      streamStatus: initialStatus,
      thinkingStarted: Boolean(initialStatus),
      failedAction: null,
      userCancelled: false,
    });
    const stillMine = () => getLive(convId).controller === controller;
    const errorContext = () => {
      const live = getLive(convId);
      const probe = (queryClient.getQueryData<Job[]>(["jobs"]) || []).find(
        (j) =>
          j.conversation_id === convId
          && j.mode === "schema_probe"
          && ["queued", "awaiting_extension", "dispatched"].includes(String(j.status || "")),
      );
      return {
        action: "generate" as const,
        lastStatus: live.streamStatus || initialStatus,
        probeActive: Boolean(probe),
        probeStep: String(probe?.current_step || ""),
      };
    };
    const isNetworkFailure = (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err || "");
      return /^(load failed|failed to fetch|networkerror when attempting to fetch resource|network request failed|the internet connection appears to be offline\.?)$/i.test(msg.trim())
        || /failed to fetch|networkerror|load failed/i.test(msg);
    };
    const readStream = async (resume: boolean): Promise<{ content: string; sawDone: boolean }> => {
      // A reconnect only follows the run it lost; if that run already ended,
      // the server answers [DONE] instead of starting a second generation.
      const target = resume ? `${url}${url.includes("?") ? "&" : "?"}resume=1` : url;
      const res = await fetch(target, { ...SSE_FETCH, signal: controller.signal });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Request failed" }));
        throw Object.assign(new Error(detailFromResponseBody(err) || "Request failed"), {
          httpStatus: res.status,
          body: err,
        });
      }
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      const { parts, sawDone } = await consumeResponseSse(
        res,
        (event, nextParts) => applySseToLive(convId, event, nextParts),
      );
      return { content: parts.content, sawDone };
    };
    const canResumeGenerate = url.includes("/generate");
    const maxAttempts = canResumeGenerate ? 20 : 1;
    let assembled = "";
    let sawDone = false;
    let attempt = 0;
    try {
      while (stillMine()) {
        attempt += 1;
        try {
          const result = await readStream(canResumeGenerate && (resumeFirst || attempt > 1));
          assembled = result.content;
          sawDone = result.sawDone;
          if (
            canResumeGenerate
            && !sawDone
            && stillMine()
            && attempt < maxAttempts
          ) {
            const priorStatus = getLive(convId).streamStatus || initialStatus;
            patchLive(convId, {
              streamStatus: priorStatus
                ? `${priorStatus.replace(/\s*·\s*reconnecting….*$/i, "")} · reconnecting…`
                : "Reconnecting to listing generation…",
              thinkingStarted: true,
              failedAction: null,
              streamText: isStreamError(assembled) ? "" : assembled,
            });
            queryClient.invalidateQueries({ queryKey: ["jobs"] });
            await sleep(Math.min(1500 * attempt, 5000));
            continue;
          }
          break;
        } catch (first) {
          // Generation continues server-side; keep reattaching through mobile drops mid-discovery.
          if (
            canResumeGenerate
            && stillMine()
            && !isAbortError(first)
            && isNetworkFailure(first)
            && attempt < maxAttempts
          ) {
            const priorStatus = getLive(convId).streamStatus || initialStatus;
            patchLive(convId, {
              streamStatus: priorStatus
                ? `${priorStatus.replace(/\s*·\s*reconnecting….*$/i, "")} · reconnecting…`
                : "Reconnecting to listing generation…",
              thinkingStarted: true,
              failedAction: null,
              streamText: "",
            });
            queryClient.invalidateQueries({ queryKey: ["jobs"] });
            await sleep(Math.min(1500 * attempt, 5000));
            continue;
          }
          throw first;
        }
      }
      if (stillMine()) {
        if (!assembled.trim() && !sawDone) {
          assembled = formatClientStreamError("Listing generation did not finish.", errorContext());
          patchLive(convId, { streamText: assembled });
        }
        if (isStreamError(assembled)) patchLive(convId, { failedAction: "generate" });
      }
    } catch (e) {
      if (isAbortError(e)) {
        if (!stillMine()) return;
        if (getLive(convId).userCancelled) {
          const restoreText = getLive(convId).lastSendText;
          const restore = getLive(convId).restoreInputOnAbort;
          patchLive(convId, {
            streamText: "",
            streamTrace: [],
            streamStatus: "",
            thinkingStarted: false,
            failedAction: null,
            streaming: false,
            generating: false,
            controller: null,
            userCancelled: false,
            restoreInputOnAbort: false,
          });
          if (restore && restoreText) setInput(restoreText);
          return;
        }
        patchLive(convId, { controller: null });
        return;
      }
      if (stillMine()) {
        const failure = e as StreamRequestError | null;
        assembled = formatClientStreamError(failure?.body || e, {
          ...errorContext(),
          httpStatus: failure?.httpStatus,
        });
        patchLive(convId, {
          streamText: assembled,
          streamTrace: [],
          streamStatus: "",
          failedAction: "generate",
        });
      }
    }
    const failed = isStreamError(assembled);
    if (stillMine()) {
      patchLive(convId, { streaming: false, generating: false, controller: null });
    }
    await refreshListingQueries(queryClient, convId);
    if (stillMine() && !failed) {
      patchLive(convId, {
        streamText: "",
        streamTrace: [],
        streamStatus: "",
        thinkingStarted: false,
      });
    }
  }, [convId, queryClient]);

  const handleGenerate = useCallback(async () => {
    pinChatToBottom();
    patchLive(convId, {
      generating: true,
      streamStatus: "Analyzing photos…",
      thinkingStarted: true,
      userCancelled: false,
    });
    await streamFromFetch(`/api/conversations/${convId}/generate`, "Analyzing photos…");
  }, [convId, streamFromFetch, pinChatToBottom]);

  const sendMessage = useCallback(async (
    text: string,
    opts?: {
      browserJobId?: string;
      browserFields?: QueuedChatMessage["browserFields"];
    },
  ) => {
    const browserContext = browserRef.current;
    const pointedAt = opts?.browserFields ?? browserContext?.fields ?? [];
    const jobId = opts?.browserJobId ?? browserContext?.jobId;
    const running = getLive(convId).streaming
      || getLive(convId).generating
      || getLive(convId).resetting
      || Boolean(queryClient.getQueryData<ConversationActivity>(["activity", convId])?.busy);
    if ((!text && !pointedAt.length) || running) return false;
    pinChatToBottom();
    const liveState = getLive(convId);
    liveState.controller?.abort();
    const controller = new AbortController();
    patchLive(convId, {
      controller,
      lastSendText: text,
      streaming: true,
      generating: false,
      streamText: "",
      streamTrace: [],
      streamStatus: "",
      thinkingStarted: true,
      failedAction: null,
      userCancelled: false,
      restoreInputOnAbort: false,
    });
    setInput("");
    const stillMine = () => getLive(convId).controller === controller;
    const errorContext = () => ({
      action: "send" as const,
      lastStatus: getLive(convId).streamStatus || "",
    });
    try {
      const res = await fetch(`/api/conversations/${convId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          text,
          ...(pointedAt.length && jobId
            ? {
                browser: {
                  job_id: jobId,
                  fields: pointedAt.map((field) => ({
                    marketplace: field.marketplace,
                    label: field.label,
                    value: field.value,
                  })),
                },
              }
            : {}),
        }),
        cache: "no-store",
        signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Request failed" }));
        if (stillMine()) {
          patchLive(convId, {
            streamText: formatClientStreamError(err, { ...errorContext(), httpStatus: res.status }),
            streamTrace: [],
            streamStatus: "",
            failedAction: "send",
            streaming: false,
            controller: null,
          });
        }
        return true;
      }
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      // Only clear live browser chips when this send used the current selection,
      // not a snapshot from a queued follow-up.
      if (!opts?.browserFields && pointedAt.length) onBrowserFieldsChange?.([]);
      const { parts } = await consumeResponseSse(res, (event, nextParts) => applySseToLive(convId, event, nextParts));
      const assembled = parts.content;
      if (stillMine() && isStreamError(assembled)) patchLive(convId, { failedAction: "send" });
      if (stillMine()) patchLive(convId, { streaming: false, controller: null });
      await refreshListingQueries(queryClient, convId);
      if (stillMine() && !isStreamError(assembled)) {
        patchLive(convId, {
          streamText: "",
          streamTrace: [],
          streamStatus: "",
          thinkingStarted: false,
        });
      }
      return true;
    } catch (e) {
      if (isAbortError(e)) {
        if (!stillMine()) return true;
        if (getLive(convId).userCancelled) {
          const restore = getLive(convId).restoreInputOnAbort;
          patchLive(convId, {
            streamText: "",
            streamTrace: [],
            streamStatus: "",
            thinkingStarted: false,
            failedAction: null,
            streaming: false,
            generating: false,
            controller: null,
            userCancelled: false,
            restoreInputOnAbort: false,
          });
          if (restore) setInput(text);
          return true;
        }
        patchLive(convId, { controller: null, streaming: false });
        return true;
      }
      if (stillMine()) {
        patchLive(convId, {
          streamText: formatClientStreamError(e, errorContext()),
          streamTrace: [],
          streamStatus: "",
          failedAction: "send",
          streaming: false,
          controller: null,
        });
      }
      return true;
    }
  }, [convId, queryClient, pinChatToBottom, onBrowserFieldsChange]);

  // Ask-chat prompts from the inspector join the same queue as typed follow-ups.
  useEffect(() => {
    if (!queuedMessage) return;
    const text = queuedMessage;
    onQueuedMessageConsumed?.();
    setPendingQueue((queue) => enqueueChatMessage(queue, newQueuedChatMessage(text)));
    pinChatToBottom();
  }, [queuedMessage, onQueuedMessageConsumed, pinChatToBottom]);

  useEffect(() => {
    if (busy || pendingQueue.length === 0 || drainLockRef.current) return;
    const liveState = getLive(convId);
    if (
      liveState.streaming
      || liveState.generating
      || liveState.resetting
      || queryClient.getQueryData<ConversationActivity>(["activity", convId])?.busy
    ) {
      return;
    }
    const { next, rest } = takeNextChatMessage(pendingQueue);
    if (!next) return;
    drainLockRef.current = true;
    setPendingQueue(rest);
    void sendMessage(next.text, {
      browserJobId: next.browserJobId,
      browserFields: next.browserFields,
    }).then((started) => {
      if (!started) {
        setPendingQueue((queue) => [next, ...queue]);
      }
    }).finally(() => {
      drainLockRef.current = false;
    });
  }, [busy, pendingQueue, sendMessage, convId, queryClient]);

  const handleCite = useCallback((citation: ChatCitation) => {
    composerRef.current?.insertCitation(citation);
    return true;
  }, []);

  const handleShowCitation = useCallback((citation: ChatCitation) => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = false;
    revealChatCitation(el, citation);
  }, []);

  const handleSend = useCallback(() => {
    const text = input.trim();
    const liveFields = browser?.fields || [];
    if (!text && !liveFields.length) return;
    if (busy) {
      // Queue plain follow-ups; browser chips ride along as a snapshot so a
      // later field pick doesn't rewrite what the user queued.
      const message = newQueuedChatMessage(text, liveFields.length
        ? {
            browserJobId: browser?.jobId,
            browserFields: liveFields.map((field) => ({
              marketplace: field.marketplace,
              label: field.label,
              value: field.value,
              key: field.key,
              filled: field.filled,
              account_managed: field.account_managed,
            })),
          }
        : undefined);
      setPendingQueue((queue) => enqueueChatMessage(queue, message));
      setInput("");
      if (liveFields.length) onBrowserFieldsChange?.([]);
      pinChatToBottom();
      return;
    }
    void sendMessage(text);
  }, [busy, input, browser, sendMessage, onBrowserFieldsChange, pinChatToBottom]);

  const handleRemoveQueued = useCallback((id: string) => {
    setPendingQueue((queue) => removeChatMessage(queue, id));
  }, []);

  const handleCancel = useCallback(async () => {
    const liveState = getLive(convId);
    setStopping(true);
    if (liveState.controller) {
      patchLive(convId, { userCancelled: true, restoreInputOnAbort: true });
      liveState.controller.abort();
    } else {
      patchLive(convId, { generating: false, streaming: false });
    }
    // A wipe already in flight finishes server-side, but Stop still means the
    // regenerate it was clearing for does not start.
    patchLive(convId, { resetting: false });
    // One stop for everything: generation, chat saves, field fills, Chrome/Vendoo jobs.
    await api.conversations.stop(convId).catch(() => undefined);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["activity", convId] }),
      queryClient.invalidateQueries({ queryKey: ["jobs"] }),
    ]);
    await refreshListingQueries(queryClient, convId);
    setStopping(false);
  }, [convId, queryClient]);

  const handleCancelDiscovery = useCallback(async () => {
    const probeId = activeProbe?.id;
    patchLive(convId, {
      userCancelled: true,
      restoreInputOnAbort: false,
      streamText: "",
      streamTrace: [],
      streamStatus: "",
      thinkingStarted: false,
      failedAction: null,
      streaming: false,
      generating: false,
      controller: null,
    });
    getLive(convId).controller?.abort();
    await Promise.allSettled([
      fetch(`/api/conversations/${convId}/generate/cancel`, { method: "POST" }),
      probeId ? api.jobs.cancel(probeId) : Promise.resolve(),
    ]);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["jobs"] }),
      queryClient.invalidateQueries({ queryKey: ["messages", convId] }),
      queryClient.invalidateQueries({ queryKey: ["conversations"] }),
    ]);
  }, [activeProbe?.id, convId, queryClient]);

  const handleRetry = useCallback(async () => {
    if (failedAction === "send" && lastSendText) {
      void sendMessage(lastSendText);
      return;
    }
    const liveText = getLive(convId).streamText || streamText || "";
    // Connection drops leave Chrome/Studio working — reattach instead of cancelling discovery.
    if (activeProbe?.id && !isResumableGenerateFailure(liveText)) {
      await handleCancelDiscovery();
    }
    void handleGenerate();
  }, [failedAction, lastSendText, sendMessage, handleGenerate, activeProbe?.id, handleCancelDiscovery, convId, streamText]);

  const hasPhotos = Boolean(photos && photos.length > 0);
  const hasMessages = Boolean(messages && messages.length > 0);
  const hasListingJson = Boolean(messages?.some((m) => {
    const json = extractJson(m.text);
    return Boolean(json && isListingJson(json));
  }) || (listing?.listing && isListingJson(JSON.stringify(listing.listing))));
  const streamFailed = isStreamError(streamText);
  const errorHint = activeProbe
    ? isResumableGenerateFailure(streamText || "")
      ? "Studio is still discovering fields in Chrome. Retry reconnects — use Cancel discovery only if Chrome is stuck."
      : `Active discovery job: ${activeProbe.current_step || activeProbe.status}. Cancel it here if Chrome is stuck, then retry.`
    : /connection dropped/i.test(streamText || "")
      ? "This is a client/network failure, not a model refusal. Retry resumes the same generate when Studio still has it running."
      : /timed out during|category selection timed out/i.test(streamText || "")
        ? "The listing model or Chrome step stalled. Retry generate. Cancel discovery only if the Listing tab shows a stuck probe."
      : /HTTP 5\d\d/i.test(streamText || "")
        ? "Studio hit an internal error before the model answered. Retry; if it keeps failing, check Studio logs or restart Studio."
        : /HTTP \d+/i.test(streamText || "")
          ? "The generate request was rejected before streaming started. Check Settings (provider sign-in / API key), then retry."
          : "";
  const streamVisible = streamFailed ? "" : assistantDisplayText(streamText);
  // Persist finishes (and messages refetch) before the SSE stream is cleared —
  // hide the live bubble once the same assistant prose is already on screen.
  const streamAlreadyPersisted = Boolean(
    streamVisible
      && messages?.some((m) => {
        if (m.role !== "assistant" && m.role !== "model") return false;
        return assistantDisplayText(m.text) === streamVisible;
      }),
  );
  const showStreamBubble = Boolean(streamText && !streamFailed && !streamAlreadyPersisted);
  const canRetry = failedAction === "send" ? Boolean(lastSendText) : Boolean(hasPhotos);
  const awaitingSellerAnswers = Boolean(
    messages?.length
      && (() => {
        const lastUserIdx = [...messages].map((m) => m.role).lastIndexOf("user");
        // Legacy conversations may still have system prompts that asked for answers.
        return messages.slice(lastUserIdx + 1).some((m) => {
          if (m.role !== "system") return false;
          return /answer the questions above|please confirm|waiting for your answers/i.test(m.text || "");
        });
      })(),
  );
  const localLabel = resetting
    ? "Clearing chat and generated fields…"
    : streaming || generating
      ? streamStatus && streamStatus !== "thinking"
        ? streamStatus
        : generating ? "Analyzing photos…" : "Answering…"
      : "";
  const activityItems = [
    ...(localLabel ? [localLabel] : []),
    ...(activity?.items || []).filter((item) => item !== localLabel),
  ];
  // Right after a local stream starts, the server may not have reported it yet.
  const activityDetail = localLabel
    ? activityItems.length > 1 ? `${localLabel} (+${activityItems.length - 1} more)` : localLabel
    : activityItems.join(" · ") || "Finishing up…";
  // T3 Code's turn header: "Working for Xs" sits in the transcript above the
  // turn's work, and what is running now is the shimmering row below it.
  const workingHeader = stopping ? (
    workStartedAtMs != null ? (
      <>Stopping — <WorkingDuration startedAtMs={workStartedAtMs} className="chat-activity-duration" /></>
    ) : "Stopping…"
  ) : workStartedAtMs != null ? (
    <>Working for <WorkingDuration startedAtMs={workStartedAtMs} className="chat-activity-duration" /></>
  ) : "Working…";
  const localLive = (streaming || generating) && !streamFailed;
  const livePhase = currentPhaseIndex(streamTrace, localLive);
  const postedIds = new Set(streamTrace.flatMap((entry) => (entry.kind === "posted" ? [entry.id] : [])));
  // Before the first status lands, or while only server work runs, the
  // header still needs a live row saying what is happening.
  const tailLabel = busy && !resetting && livePhase < 0 && !showStreamBubble
    ? localLive ? localLabel || activityDetail : activityDetail
    : "";
  const lastTraceIndex = streamTrace.length - 1;
  function renderTraceEntry(entry: LiveTraceEntry, index: number) {
    if (entry.kind === "posted") {
      const message = messages?.find((m) => String(m.id) === entry.id);
      return message ? renderMessage(message) : null;
    }
    if (entry.kind === "thought") {
      return <LiveThought key={`thought-${index}`} text={entry.text} live={localLive && index === lastTraceIndex} />;
    }
    if (entry.kind === "step") {
      return <LiveActivityRow key={`step-${index}`} icon={<SearchIcon />} label={entry.text} live={false} />;
    }
    const live = index === livePhase;
    // One moving highlight at a time: a live thought under this phase has it.
    const thinkingNow = localLive && streamTrace[lastTraceIndex]?.kind === "thought";
    return (
      <LiveActivityRow
        key={`phase-${index}`}
        icon={<PhaseIcon live={live} />}
        label={live ? entry.text : settledPhaseText(entry.text)}
        live={live && !thinkingNow}
      />
    );
  }
  const composerPlaceholder = busy
    ? "Queue a follow-up…"
    : browser
    ? "Tell Studio what to fix in the Vendoo draft..."
    : !hasPhotos
    ? "Upload photos to begin"
    : awaitingSellerAnswers
      ? "Answer the questions above..."
      : hasMessages
        ? "Refine the listing..."
        : "Add a note, or generate the listing...";
  const canSubmit = Boolean(input.trim() || browser?.fields.length);

  useEffect(() => {
    const reattach = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      const liveState = getLive(convId);
      if (liveState.controller || liveState.userCancelled || !liveState.generating) return;
      const start = liveState.startQueued;
      liveState.startQueued = false;
      void streamFromFetch(
        `/api/conversations/${convId}/generate`,
        liveState.streamStatus || "Analyzing photos…",
        !start,
      );
    };
    document.addEventListener("visibilitychange", reattach);
    reattach();
    return () => document.removeEventListener("visibilitychange", reattach);
  }, [convId, generating, streamFromFetch]);

  useEffect(() => {
    const liveState = getLive(convId);
    if (!liveState.generating || liveState.controller) return;
    if (!hasListingJson) return;
    patchLive(convId, {
      generating: false,
      streaming: false,
      streamText: "",
      streamTrace: [],
      streamStatus: "",
      thinkingStarted: false,
    });
  }, [convId, hasListingJson]);

  function renderMessage(m: Message) {
    if (m.role === "user") {
      return (
        <div key={m.id} className="msg msg-user">
          <ChatMarkdown text={m.text} lineBreaks />
        </div>
      );
    }

    if (m.role === "system" && isPhotoAnalysis(m.text)) {
      return <EvidenceCard key={m.id} text={m.text} messageId={m.id} />;
    }

    if (m.role === "system" && isCompResearch(m.text)) {
      return <SoldCompsCard key={m.id} text={m.text} messageId={m.id} />;
    }

    if (m.role === "system") {
      return (
        <div key={m.id} style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "4px 0" }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--color-border-bright)", flexShrink: 0, marginTop: 6 }} />
          <span className="msg-system" style={{ padding: 0, borderLeft: "none", maxWidth: "none", whiteSpace: "pre-wrap" }}>{m.text}</span>
        </div>
      );
    }

    const visible = assistantDisplayText(m.text);
    if (!visible) return null;
    return (
      <div key={m.id} className="msg msg-assistant" data-citation-source={m.id}>
        <ChatMarkdown text={visible} />
      </div>
    );
  }

  return (
    <ChatCitationRevealContext value={handleShowCitation}>
    <div className="chat-panel">
      <div
        ref={setScrollEl}
        className="chat-scroll"
        onScroll={handleChatScroll}
        onWheel={handleChatWheel}
        onTouchStart={handleChatTouchStart}
        onTouchMove={handleChatTouchMove}
      >
        {isLoading && !hasMessages && (
          <div className="empty-state" style={{ padding: "16px 0" }}><p className="text-xs text-muted">Loading...</p></div>
        )}

        {!isLoading && !hasMessages && !busy && !streamFailed && (
          <div className="empty-state" style={{ padding: "32px 16px" }}>
            <h3 style={{ fontStyle: "italic", fontSize: 20, marginBottom: 4, lineHeight: 1.2 }}>Generate a Listing</h3>
            {hasPhotos ? (
              <>
                <p className="text-xs text-muted">{photos?.length ?? 0} photo{photos?.length !== 1 ? "s" : ""} uploaded</p>
                <button type="button" className="btn btn-primary" onClick={handleGenerate} disabled={generating || streaming} style={{ marginTop: 12, padding: "9px 22px" }}>
                  Generate Listing
                </button>
              </>
            ) : (
              <p className="text-xs text-muted">Upload photos to begin</p>
            )}
          </div>
        )}

        {messages?.filter((m) => !postedIds.has(String(m.id))).map(renderMessage)}

        {resetting && (
          <div className="thinking-block">
            <SendProgress label="Clearing chat and generated fields…" />
          </div>
        )}

        {busy || streamTrace.length > 0 ? (
          <div className="live-work" role="status" aria-live="polite">
            {busy ? <div className="live-work-header">{workingHeader}</div> : null}
            {streamTrace.map(renderTraceEntry)}
            {tailLabel ? <LiveActivityRow icon={<PhaseIcon live />} label={tailLabel} live /> : null}
          </div>
        ) : null}

        {showStreamBubble && (
          streamVisible ? (
            <div className="msg msg-assistant">
              <ChatMarkdown text={streamVisible} isStreaming />
            </div>
          ) : livePhase >= 0 ? null : (
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: 8 }}>
              <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--color-cobalt)", flexShrink: 0 }} />
              <span className="text-xs text-muted">UPDATING LISTING…</span>
            </div>
          )
        )}

        {pendingQueue.map((queued, index) => (
          <div
            key={queued.id}
            className="msg msg-user msg-queued"
            data-queued-message-id={queued.id}
          >
            {queued.text ? (
              <ChatMarkdown text={queued.text} lineBreaks />
            ) : (
              <span className="msg-queued-empty">
                {queued.browserFields?.length
                  ? `${queued.browserFields.length} field${queued.browserFields.length === 1 ? "" : "s"} from the browser`
                  : "Empty message"}
              </span>
            )}
            <div className="msg-queued-meta">
              <span className="msg-queued-label" title={index === 0
                ? "Sends when the current run finishes"
                : "Sends after the messages above it"}
              >
                Queued
              </span>
              <button
                type="button"
                className="msg-queued-remove"
                aria-label="Remove queued message"
                onClick={() => handleRemoveQueued(queued.id)}
              >
                ×
              </button>
            </div>
          </div>
        ))}

        {streamFailed && (
          <div className="chat-error" role="alert">
            <div className="chat-error-text">{streamText}</div>
            {errorHint ? <div className="chat-error-hint">{errorHint}</div> : null}
            <div className="chat-error-actions">
              {activeProbe ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => { void handleCancelDiscovery(); }}
                  disabled={busy}
                >
                  Cancel discovery
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => { void handleRetry(); }}
                disabled={busy || !canRetry}
              >
                {busy ? "Retrying..." : activeProbe ? "Cancel & retry" : "Retry"}
              </button>
            </div>
          </div>
        )}

        {hasMessages && !hasListingJson && !streamFailed && !busy && hasPhotos && (
          <div className="chat-error">
            <div className="chat-error-text">Listing generation did not finish.</div>
            {activeProbe ? (
              <div className="chat-error-hint">
                Field discovery is still running in Chrome. Cancel it from here, then retry.
              </div>
            ) : null}
            <div className="chat-error-actions">
              {activeProbe ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => { void handleCancelDiscovery(); }}
                >
                  Cancel discovery
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => { void (activeProbe ? handleRetry() : handleGenerate()); }}
              >
                {activeProbe ? "Cancel & retry" : "Retry"}
              </button>
            </div>
          </div>
        )}

        {hasMessages && !busy && !streamFailed && (
          <div className="chat-activity is-done" role="status">
            <span className="chat-activity-dot" aria-hidden="true" />
            <span className="chat-activity-text">Done — nothing running</span>
          </div>
        )}
      </div>

      <CiteSelectionToolbar viewport={chatViewport} onCite={handleCite} />

      <div className="chat-composer">
        {browser && browser.fields.length > 0 && (
          <div className="chat-browser-fields" aria-label="Fields pointed at in the Vendoo browser">
            <div className="chat-browser-fields-head">
              <span>
                {browser.fields.length} field{browser.fields.length === 1 ? "" : "s"} from the browser go with your message
              </span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => onBrowserFieldsChange?.([])}>
                Clear
              </button>
            </div>
            <div className="chat-browser-fields-list">
              {browser.fields.map((field) => (
                <span
                  key={`${field.marketplace}:${field.key || field.label}`}
                  className={`browser-chip${field.account_managed ? " is-muted" : ""}`}
                  title={field.value || "empty"}
                >
                  {MARKET_LABELS[field.marketplace] || field.marketplace} / {field.label}
                  {!field.filled && <span className="browser-chip-empty">empty</span>}
                  <button
                    type="button"
                    aria-label={`Remove ${field.label}`}
                    onClick={() => onBrowserFieldsChange?.(browser.fields.filter((item) => item !== field))}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}
        <div className="chat-composer-pill">
          <ComposerPromptEditor
            handleRef={composerRef}
            value={input}
            onChange={setInput}
            onSubmit={handleSend}
            placeholder={composerPlaceholder}
          />
          {busy ? (
            <>
              <button
                type="button"
                className="chat-send chat-send-cancel"
                onClick={() => { void handleCancel(); }}
                disabled={stopping}
                aria-label="Stop"
                title="Stop everything running for this listing"
              >
                <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor" aria-hidden="true">
                  <rect x="1" y="1" width="8" height="8" rx="1" />
                </svg>
              </button>
              {canSubmit ? (
                <button
                  type="button"
                  className="chat-send"
                  onClick={handleSend}
                  aria-label="Queue message"
                  title="Queue for when the current run finishes"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M8 12.5V3.5M8 3.5L3.5 8M8 3.5L12.5 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              ) : null}
            </>
          ) : (
            <button
              type="button"
              className="chat-send"
              onClick={handleSend}
              disabled={!canSubmit}
              aria-label="Send"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M8 12.5V3.5M8 3.5L3.5 8M8 3.5L12.5 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
    </ChatCitationRevealContext>
  );
}
