import { Fragment, useState, useRef, useEffect, useCallback } from "react";
import { newQueuedChatMessage } from "./chatMessageQueue";
import { CiteSelectionToolbar } from "./CiteSelectionToolbar";
import { type ChatCitation } from "./chatCitations";
import { ChatCitationRevealContext } from "./chatCitationContext";
import { type ComposerPromptEditorHandle } from "./ComposerPromptEditor";
import { currentPhaseIndex } from "./liveTrace";
import { assistantDisplayText, extractJson, isListingJson, isStreamError } from "./chat/assistantDisplay";
import { BrowserFieldChips } from "./chat/BrowserFieldChips";
import { useComposerImages } from "./chat/ComposerAttachments";
import { ChatComposer } from "./chat/ChatComposer";
import { StreamErrorPanel, UnfinishedGenerationPanel } from "./chat/ChatErrors";
import { ChatMessage } from "./chat/ChatMessage";
import { ChatTranscript } from "./chat/ChatTranscript";
import { FoldedTurnRow, LiveWork } from "./chat/LiveWork";
import { QueuedMessages } from "./chat/QueuedMessages";
import { AssistantMessageRow } from "./chat/TimelineRows";
import { WorkingRow } from "./chat/TimelineRows";
import { useChatQueue } from "./chat/useChatQueue";
import { useChatScroll } from "./chat/useChatScroll";
import { useChatServerState } from "./chat/useChatServerState";
import { useChatStream, useGenerateRecovery } from "./chat/useChatStream";
import { useLiveChat } from "./chat/useLiveChat";
import { placeTurnFolds } from "./chat/workLogModel";
import { Button } from "./ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "./ui/empty";
import { Spinner } from "./ui/spinner";
import type { BrowserField } from "../api/client";
import type { Message } from "../api/types";

interface Props {
  convId: string;
  queuedMessage?: string | null;
  onQueuedMessageConsumed?: () => void;
  /** Draft open in Studio's browser. Messages then go to the fix agent with the picked fields. */
  browser?: { jobId: string; fields: BrowserField[] } | null;
  onBrowserFieldsChange?: (fields: BrowserField[]) => void;
}

/** Spacing under each transcript row, as T3 Code's TimelineRowContent pads them. */
function messageRowPadding(m: Message): string {
  return m.role === "user" || m.role === "assistant" || m.role === "model" ? "pb-4" : "pb-2";
}

export function ChatPanel({ convId, queuedMessage, onQueuedMessageConsumed, browser, onBrowserFieldsChange }: Props) {
  const [input, setInput] = useState("");
  // Quotes live inline in the composer itself, T3 Code style: `input` is the
  // markdown prompt, and a quote inside it is its citation link.
  const composerRef = useRef<ComposerPromptEditorHandle | null>(null);
  const images = useComposerImages();
  const clearImages = images.clear;

  useEffect(() => {
    setInput("");
    clearImages();
  }, [convId, clearImages]);
  const {
    streaming,
    generating,
    streamText,
    streamTrace,
    streamAgents,
    foldedTurns,
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
  const scroll = useChatScroll(convId);
  const { pinChatToBottom, chatViewport, handleShowCitation } = scroll;
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
    const attached = images.images;
    if (!text && !liveFields.length && !attached.length) return;
    if (busy) {
      if (attached.length) {
        images.setError("Images go with the next message once the current run finishes.");
        return;
      }
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
    void (async () => {
      const uploads = attached.length ? await images.prepare(attached) : [];
      if (uploads === null) return;
      images.clear();
      void sendMessage(text, uploads.length ? { attachments: uploads } : undefined);
    })();
  }, [busy, input, browser, sendMessage, onBrowserFieldsChange, pinChatToBottom, enqueue, images]);

  const hasPhotos = Boolean(photos && photos.length > 0);
  const hasMessages = Boolean(messages && messages.length > 0);
  const hasListingJson = Boolean(messages?.some((m) => {
    const json = extractJson(m.text);
    return Boolean(json && isListingJson(json));
  }) || (listing?.listing && isListingJson(JSON.stringify(listing.listing))));
  const streamFailed = isStreamError(streamText);
  const streamVisible = streamFailed ? "" : assistantDisplayText(streamText);
  // Persist finishes (and messages refetch) before the SSE stream is cleared —
  // hide the live reply once the same assistant prose is already on screen.
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
  // log still needs a live row saying what is happening.
  const tailLabel = busy && !resetting && livePhase < 0 && !showStreamBubble
    ? localLive ? localLabel || activityDetail : activityDetail
    : "";
  const composerPlaceholder = busy
    ? "Queue a follow-up…"
    : browser
    ? "Tell Anteroom what to fix in the Vendoo draft..."
    : !hasPhotos
    ? "Upload photos to begin"
    : awaitingSellerAnswers
      ? "Answer the questions above..."
      : hasMessages
        ? "Refine the listing..."
        : "Add a note, or generate the listing...";

  useGenerateRecovery(convId, generating, hasListingJson, streamFromFetch);

  const allMessages = messages ?? [];
  const foldPositions = placeTurnFolds(allMessages, foldedTurns);
  const foldsAt = (index: number) => foldedTurns.filter((_, k) => foldPositions[k] === index);
  const renderPosted = (id: string) => {
    const message = allMessages.find((m) => String(m.id) === id);
    return message ? <ChatMessage message={message} /> : null;
  };

  return (
    <ChatCitationRevealContext value={handleShowCitation}>
    <div className="chat-panel flex min-h-0 flex-1 flex-col">
      <ChatTranscript scroll={scroll}>
        {isLoading && !hasMessages ? (
          <div className="flex justify-center py-4"><Spinner size="md" tone="muted" /></div>
        ) : null}

        {!isLoading && !hasMessages && !busy && !streamFailed ? (
          <Empty className="py-8">
            <EmptyHeader>
              <EmptyTitle>Generate a listing</EmptyTitle>
              <EmptyDescription>
                {hasPhotos
                  ? `${photos?.length ?? 0} photo${photos?.length !== 1 ? "s" : ""} uploaded`
                  : "Upload photos to begin"}
              </EmptyDescription>
            </EmptyHeader>
            {hasPhotos ? (
              <EmptyContent>
                <Button type="button" onClick={handleGenerate} disabled={generating || streaming}>
                  Generate listing
                </Button>
              </EmptyContent>
            ) : null}
          </Empty>
        ) : null}

        {allMessages.map((m, index) => (
          <Fragment key={m.id}>
            {foldsAt(index).map((turn) => <FoldedTurnRow key={turn.id} turn={turn} />)}
            {postedIds.has(String(m.id)) ? null : (
              <div className={messageRowPadding(m)} data-message-id={m.id}>
                <ChatMessage message={m} />
              </div>
            )}
          </Fragment>
        ))}
        {foldsAt(allMessages.length).map((turn) => <FoldedTurnRow key={turn.id} turn={turn} />)}

        {resetting ? (
          <div className="pb-1.5"><WorkingRow startedAtMs={workStartedAtMs} /></div>
        ) : null}

        {(busy && !resetting) || streamTrace.length > 0 ? (
          <LiveWork
            busy={busy}
            stopping={stopping}
            workStartedAtMs={workStartedAtMs}
            streamTrace={streamTrace}
            streamAgents={streamAgents}
            localLive={localLive}
            tailLabel={tailLabel}
            renderPosted={renderPosted}
          />
        ) : null}

        {showStreamBubble && streamVisible ? (
          <div className="pb-4">
            <AssistantMessageRow text={streamVisible} streaming />
          </div>
        ) : null}

        <QueuedMessages queue={pendingQueue} onRemove={handleRemoveQueued} />

        {streamFailed ? (
          <StreamErrorPanel
            streamText={streamText}
            activeProbe={activeProbe}
            busy={busy}
            canRetry={canRetry}
            onCancelDiscovery={handleCancelDiscovery}
            onRetry={handleRetry}
          />
        ) : null}

        {hasMessages && !hasListingJson && !streamFailed && !busy && hasPhotos ? (
          <UnfinishedGenerationPanel
            probeActive={Boolean(activeProbe)}
            onCancelDiscovery={handleCancelDiscovery}
            onRetry={() => (activeProbe ? handleRetry() : handleGenerate())}
          />
        ) : null}
      </ChatTranscript>

      <CiteSelectionToolbar viewport={chatViewport} onCite={handleCite} />

      <ChatComposer
        composerRef={composerRef}
        input={input}
        onInputChange={setInput}
        placeholder={composerPlaceholder}
        images={images}
        header={<BrowserFieldChips fields={browser?.fields} onChange={onBrowserFieldsChange} />}
        hasOtherContent={Boolean(browser?.fields?.length)}
        busy={busy}
        stopping={stopping}
        onSend={handleSend}
        onStop={() => { void handleCancel(); }}
        stopLabel="Stop everything running for this listing"
      />
    </div>
    </ChatCitationRevealContext>
  );
}
