import React, { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { BrowserField } from "../../api/client";
import type { ConversationActivity, Job } from "../../api/types";
import { type QueuedChatMessage } from "../chatMessageQueue";
import { isStreamError } from "./assistantDisplay";
import {
  consumeResponseSse,
  detailFromResponseBody,
  formatClientStreamError,
  isAbortError,
  isResumableGenerateFailure,
  sleep,
  SSE_FETCH,
  type StreamRequestError,
} from "./chatStream";
import { applySseToLive, foldLiveTurn, getLive, patchLive, refreshListingQueries } from "./liveChatStore";
import type { ChatAttachmentUpload, Message } from "../../api/types";

interface Options {
  convId: string;
  browser: { jobId: string; fields: BrowserField[] } | null | undefined;
  onBrowserFieldsChange: ((fields: BrowserField[]) => void) | undefined;
  setInput: React.Dispatch<React.SetStateAction<string>>;
  pinChatToBottom: () => void;
  activeProbe: Job | undefined;
  failedAction: "generate" | "send" | null;
  lastSendText: string;
  streamText: string;
}

/** Generate, send, stop and retry for one listing's chat, all writing to the live store. */
export function useChatStream({
  convId,
  browser,
  onBrowserFieldsChange,
  setInput,
  pinChatToBottom,
  activeProbe,
  failedAction,
  lastSendText,
  streamText,
}: Options) {
  const browserRef = useRef(browser);
  browserRef.current = browser;
  const [stopping, setStopping] = useState(false);
  const queryClient = useQueryClient();
  // The turn's own messages are whatever arrives after these; its fold row sits above them.
  const messageIds = useCallback(
    () => (queryClient.getQueryData<Message[]>(["messages", convId]) || []).map((m) => String(m.id)),
    [convId, queryClient],
  );

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
      turnBaseIds: messageIds(),
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
          foldLiveTurn(convId, { cancelled: true });
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
      // The answer stays on screen below "Worked for"; a failure keeps its work in view.
      if (!failed) foldLiveTurn(convId);
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
  }, [convId, queryClient, setInput, messageIds]);

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
      attachments?: ChatAttachmentUpload[];
    },
  ) => {
    const browserContext = browserRef.current;
    const pointedAt = opts?.browserFields ?? browserContext?.fields ?? [];
    const jobId = opts?.browserJobId ?? browserContext?.jobId;
    const running = getLive(convId).streaming
      || getLive(convId).generating
      || getLive(convId).resetting
      || Boolean(queryClient.getQueryData<ConversationActivity>(["activity", convId])?.busy);
    const attachments = opts?.attachments ?? [];
    if ((!text && !pointedAt.length && !attachments.length) || running) return false;
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
      turnBaseIds: messageIds(),
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
          ...(attachments.length ? { attachments } : {}),
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
      if (stillMine()) {
        patchLive(convId, { streaming: false, controller: null });
        if (!isStreamError(assembled)) foldLiveTurn(convId);
      }
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
          foldLiveTurn(convId, { cancelled: true });
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
  }, [convId, queryClient, pinChatToBottom, onBrowserFieldsChange, setInput, messageIds]);

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

  return { stopping, streamFromFetch, handleGenerate, sendMessage, handleCancel, handleCancelDiscovery, handleRetry };
}

/** Reattach to a generate the server is still running, and settle one that already finished. */
export function useGenerateRecovery(
  convId: string,
  generating: boolean,
  hasListingJson: boolean,
  streamFromFetch: (url: string, initialStatus?: string, resumeFirst?: boolean) => Promise<void>,
) {
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
}
