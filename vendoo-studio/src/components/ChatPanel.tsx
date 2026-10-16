import { useState, useRef, useEffect, useCallback } from "react";
import { ChatMarkdown } from "./ChatMarkdown";
import { newQueuedChatMessage } from "./chatMessageQueue";
import { CiteSelectionToolbar } from "./CiteSelectionToolbar";
import { type ChatCitation } from "./chatCitations";
import { ChatCitationRevealContext } from "./chatCitationContext";
import { type ComposerPromptEditorHandle } from "./ComposerPromptEditor";
import { SendProgress } from "./SendProgress";
import { currentPhaseIndex } from "./liveTrace";
import { assistantDisplayText, extractJson, isListingJson, isStreamError } from "./chat/assistantDisplay";
import { ChatComposer } from "./chat/ChatComposer";
import { StreamErrorPanel, UnfinishedGenerationPanel } from "./chat/ChatErrors";
import { ChatMessage } from "./chat/ChatMessage";
import { LiveWork } from "./chat/LiveWork";
import { QueuedMessages } from "./chat/QueuedMessages";
import { useChatQueue } from "./chat/useChatQueue";
import { useChatScroll } from "./chat/useChatScroll";
import { useChatServerState } from "./chat/useChatServerState";
import { useChatStream, useGenerateRecovery } from "./chat/useChatStream";
import { useLiveChat } from "./chat/useLiveChat";
import type { BrowserField } from "../api/client";

interface Props {
  convId: string;
  queuedMessage?: string | null;
  onQueuedMessageConsumed?: () => void;
  /** Draft open in Studio's browser. Messages then go to the fix agent with the picked fields. */
  browser?: { jobId: string; fields: BrowserField[] } | null;
  onBrowserFieldsChange?: (fields: BrowserField[]) => void;
}

export function ChatPanel({ convId, queuedMessage, onQueuedMessageConsumed, browser, onBrowserFieldsChange }: Props) {
  const [input, setInput] = useState("");
  // Quotes live inline in the composer itself, T3 Code style: `input` is the
  // markdown prompt, and a quote inside it is its citation link.
  const composerRef = useRef<ComposerPromptEditorHandle | null>(null);

  useEffect(() => {
    setInput("");
  }, [convId]);
  const {
    streaming,
    generating,
    streamText,
    streamTrace,
    streamStatus,
    resetting,
    workStartedAtMs,
    failedAction,
    lastSendText,
  } = useLiveChat(convId);
  const { activity, messages, isLoading, photos, listing, activeProbe, busy } = useChatServerState(
    convId,
    streaming || generating || resetting,
  );
  const {
    chatViewport,
    setScrollEl,
    pinChatToBottom,
    handleChatScroll,
    handleChatWheel,
    handleChatTouchStart,
    handleChatTouchMove,
    handleShowCitation,
  } = useChatScroll(convId, messages, streamText, streamTrace);
  const {
    stopping,
    streamFromFetch,
    handleGenerate,
    sendMessage,
    handleCancel,
    handleCancelDiscovery,
    handleRetry,
  } = useChatStream({
    convId,
    browser,
    onBrowserFieldsChange,
    setInput,
    pinChatToBottom,
    activeProbe,
    failedAction,
    lastSendText,
    streamText,
  });
  const { pendingQueue, enqueue, handleRemoveQueued } = useChatQueue({
    convId,
    busy,
    sendMessage,
    queuedMessage,
    onQueuedMessageConsumed,
    pinChatToBottom,
  });

  const handleCite = useCallback((citation: ChatCitation) => {
    composerRef.current?.insertCitation(citation);
    return true;
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
      enqueue(message);
      setInput("");
      if (liveFields.length) onBrowserFieldsChange?.([]);
      pinChatToBottom();
      return;
    }
    void sendMessage(text);
  }, [busy, input, browser, sendMessage, onBrowserFieldsChange, pinChatToBottom, enqueue]);

  const hasPhotos = Boolean(photos && photos.length > 0);
  const hasMessages = Boolean(messages && messages.length > 0);
  const hasListingJson = Boolean(messages?.some((m) => {
    const json = extractJson(m.text);
    return Boolean(json && isListingJson(json));
  }) || (listing?.listing && isListingJson(JSON.stringify(listing.listing))));
  const streamFailed = isStreamError(streamText);
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
  const localLive = (streaming || generating) && !streamFailed;
  const livePhase = currentPhaseIndex(streamTrace, localLive);
  const postedIds = new Set(streamTrace.flatMap((entry) => (entry.kind === "posted" ? [entry.id] : [])));
  // Before the first status lands, or while only server work runs, the
  // header still needs a live row saying what is happening.
  const tailLabel = busy && !resetting && livePhase < 0 && !showStreamBubble
    ? localLive ? localLabel || activityDetail : activityDetail
    : "";
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

  useGenerateRecovery(convId, generating, hasListingJson, streamFromFetch);

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

        {messages?.filter((m) => !postedIds.has(String(m.id))).map((m) => <ChatMessage key={m.id} message={m} />)}

        {resetting && (
          <div className="thinking-block">
            <SendProgress label="Clearing chat and generated fields…" />
          </div>
        )}

        {busy || streamTrace.length > 0 ? (
          <LiveWork
            busy={busy}
            stopping={stopping}
            workStartedAtMs={workStartedAtMs}
            streamTrace={streamTrace}
            messages={messages}
            localLive={localLive}
            livePhase={livePhase}
            tailLabel={tailLabel}
          />
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

        <QueuedMessages queue={pendingQueue} onRemove={handleRemoveQueued} />

        {streamFailed && (
          <StreamErrorPanel
            streamText={streamText}
            activeProbe={activeProbe}
            busy={busy}
            canRetry={canRetry}
            onCancelDiscovery={handleCancelDiscovery}
            onRetry={handleRetry}
          />
        )}

        {hasMessages && !hasListingJson && !streamFailed && !busy && hasPhotos && (
          <UnfinishedGenerationPanel
            probeActive={Boolean(activeProbe)}
            onCancelDiscovery={handleCancelDiscovery}
            onRetry={() => (activeProbe ? handleRetry() : handleGenerate())}
          />
        )}

        {hasMessages && !busy && !streamFailed && (
          <div className="chat-activity is-done" role="status">
            <span className="chat-activity-dot" aria-hidden="true" />
            <span className="chat-activity-text">Done — nothing running</span>
          </div>
        )}
      </div>

      <CiteSelectionToolbar viewport={chatViewport} onCite={handleCite} />

      <ChatComposer
        composerRef={composerRef}
        input={input}
        onInputChange={setInput}
        placeholder={composerPlaceholder}
        browserFields={browser?.fields}
        onBrowserFieldsChange={onBrowserFieldsChange}
        busy={busy}
        stopping={stopping}
        onSend={handleSend}
        onCancel={handleCancel}
      />
    </div>
    </ChatCitationRevealContext>
  );
}
