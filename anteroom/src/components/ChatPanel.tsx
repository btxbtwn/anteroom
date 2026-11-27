import { Fragment, useRef, useEffect, useLayoutEffect, useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { addToast } from "../ui/toast";
import { api } from "../api/client";
import { photosUploading, usePhotosUploading } from "../photoUploads";
import { CiteSelectionToolbar } from "./CiteSelectionToolbar";
import { type ChatCitation } from "./chatCitations";
import { ChatCitationRevealContext } from "./chatCitationContext";
import { type ComposerPromptEditorHandle } from "./ComposerPromptEditor";
import { currentPhaseIndex } from "./liveTrace";
import { assistantDisplayText, extractJson, isListingJson, isStreamError } from "./chat/assistantDisplay";
import { useComposerImages } from "./chat/ComposerAttachments";
import { getComposerDraft, setComposerDraftText, setComposerListingPhotoBatch, useComposerDraftText } from "./chat/composerDrafts";
import { chatQueue, editChatMessage, newQueuedChatMessage, resolveFollowUp } from "./chat/chatQueueStore";
import { modifierKeyLabel } from "./chat/ComposerPrimaryActions";
import { ChatComposer } from "./chat/ChatComposer";
import { decideComposerImageRoute, uploadComposerDraftPhotos } from "./chat/composerDraftPhotos";
import { StreamErrorPanel, UnfinishedGenerationPanel } from "./chat/ChatErrors";
import { ChatMessage } from "./chat/ChatMessage";
import { ChatTranscript } from "./chat/ChatTranscript";
import { FoldedTurnRow, LiveWork } from "./chat/LiveWork";
import { QueuedMessages } from "./chat/QueuedMessages";
import { StreamingAssistantRow, WorkingRow } from "./chat/TimelineRows";
import { getLive } from "./chat/liveChatStore";
import { useChatQueue } from "./chat/useChatQueue";
import { useChatScroll } from "./chat/useChatScroll";
import { useChatServerState } from "./chat/useChatServerState";
import { canSteerLive, steerableChat, useChatStream, useGenerateRecovery } from "./chat/useChatStream";
import { useWorkspaceRuntime } from "../app/runtime";
import { useLiveChat } from "./chat/useLiveChat";
import { placeTurnFolds } from "./chat/workLogModel";
import { RegenerateListingButton } from "./ClearListingButton";
import { Button } from "./ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "./ui/empty";
import { Spinner } from "./ui/spinner";
import type { ChatAttachmentUpload, ConversationActivity, ListingDocument, Message } from "../api/types";
import type { ListingChatStatus } from "./ListingChatPopup";

interface Props {
  convId: string;
  queuedMessage?: string | null;
  onQueuedMessageConsumed?: () => void;
  /** What the chat is doing, for a minimized chat's pill. Reported once messages load. */
  onStatusChange?: (status: ListingChatStatus) => void;
}

/** Spacing under each transcript row, as T3 Code's TimelineRowContent pads them. */
function messageRowPadding(m: Message): string {
  return m.role === "user" || m.role === "assistant" || m.role === "model" ? "pb-4" : "pb-2";
}

const preparingDrafts = new Set<string>();

export function ChatPanel({ convId, queuedMessage, onQueuedMessageConsumed, onStatusChange }: Props) {
  const queryClient = useQueryClient();
  const runtime = useWorkspaceRuntime();
  const uploadingPhotos = usePhotosUploading(convId);
  const draftKey = `listing:${convId}`;

  const [input, setInput] = useComposerDraftText(draftKey);
  // Quotes live inline in the composer itself, T3 Code style: `input` is the
  // markdown prompt, and a quote inside it is its citation link.
  const composerRef = useRef<ComposerPromptEditorHandle | null>(null);
  const images = useComposerImages(draftKey);
  const {
    streaming,
    generating,
    streamText,
    streamReceived,
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
    interruptAnswer,
  } = useChatStream({
    convId,
    setInput,
    pinChatToBottom,
    activeProbe,
    failedAction,
    lastSendText,
    streamText,
  });
  const serverBusy = Boolean(activity?.busy);
  const sendQueuedMessage = useCallback(
    async (text: string, attachments: ChatAttachmentUpload[] = []) => {
      // Ask the server first: the activity on hand can be seconds old, and a
      // reply just cut off by a steer saves its words as it unwinds. Sending
      // before it has would put the follow-up ahead of that reply.
      try {
        const fresh = await api.conversations.activity(convId);
        queryClient.setQueryData(["activity", convId], fresh);
        if (fresh.busy) return false;
      } catch {
        // Unreachable right now: go on what is cached, as before, rather than retry in a loop.
      }
      return sendMessage(text, { preserveInput: true, ...(attachments.length ? { attachments } : {}) });
    },
    [convId, queryClient, sendMessage],
  );
  // A queued message being edited in the box, as T3 Code edits a queued run in its composer.
  // Keyed by listing, so another listing never inherits it.
  const [editing, setEditing] = useState<{ convId: string; id: string } | null>(null);
  const editingQueuedId = editing?.convId === convId ? editing.id : null;
  const setEditingQueuedId = useCallback(
    (id: string | null) => setEditing(id ? { convId, id } : null),
    [convId],
  );
  const { key: queueKey, pendingQueue, sending: queueSending, enqueue, steer, sendNow, handleRemoveQueued } = useChatQueue({
    convId,
    busy,
    serverBusy,
    paused: editingQueuedId !== null,
    sendMessage: sendQueuedMessage,
    interruptAnswer,
    queuedMessage,
    onQueuedMessageConsumed,
    pinChatToBottom,
  });
  // Leaving an edit (another listing, a closed chat) gives the queued message
  // back unchanged and leaves no half-edited copy in this listing's box.
  const editDraftKey = useRef<string | null>(null);
  useEffect(() => () => {
    if (editDraftKey.current !== draftKey) return;
    editDraftKey.current = null;
    setComposerDraftText(draftKey, "");
    setEditing(null);
  }, [draftKey]);

  const startEditQueued = useCallback((id: string) => {
    const message = chatQueue(queueKey).find((item) => item.id === id);
    if (!message || input.trim() || message.payload !== undefined || message.steer) return false;
    editDraftKey.current = draftKey;
    setEditingQueuedId(id);
    setInput(message.text);
    composerRef.current?.focus();
    return true;
  }, [draftKey, queueKey, input, setInput, setEditingQueuedId]);
  const cancelEditQueued = useCallback(() => {
    editDraftKey.current = null;
    setEditingQueuedId(null);
    setInput("");
  }, [setInput, setEditingQueuedId]);
  const editLastQueued = useCallback(() => {
    const last = pendingQueue[pendingQueue.length - 1];
    return Boolean(last && !editingQueuedId && startEditQueued(last.id));
  }, [pendingQueue, editingQueuedId, startEditQueued]);

  const handleCite = useCallback((citation: ChatCitation) => {
    composerRef.current?.insertCitation(citation);
    return true;
  }, []);

  const hasPhotos = Boolean(photos && photos.length > 0);
  const hasMessages = Boolean(messages && messages.length > 0);
  const checkListingCopy = useCallback(
    (currentMessages: readonly Message[] | undefined, currentListing: ListingDocument | undefined) =>
      Boolean(
        currentMessages?.some((m) => {
          const json = extractJson(m.text);
          return Boolean(json && isListingJson(json));
        }) || (currentListing?.listing && isListingJson(JSON.stringify(currentListing.listing))),
      ),
    [],
  );
  const hasListingJson = checkListingCopy(messages, listing);
  const steerable = steerableChat(runtime.hosted, hasListingJson);
  const canSteer = steerable
    && streaming && !generating && !resetting && !/^saving/i.test(streamStatus.trim());

  const sendFirstQueuedNow = useCallback(() => {
    const first = pendingQueue[0];
    if (!first || first.steer || !steerable || !canSteerLive(convId) || editingQueuedId) return false;
    void sendNow(first.id);
    return true;
  }, [pendingQueue, steerable, convId, editingQueuedId, sendNow]);
  const hasListingJsonRef = useRef(hasListingJson);
  useLayoutEffect(() => { hasListingJsonRef.current = hasListingJson; }, [hasListingJson]);

  const hasCurrentListingCopy = useCallback(async () => {
    if (hasListingJsonRef.current) return true;
    const latestMessages = queryClient.getQueryData<Message[]>(["messages", convId]);
    let latestListing = queryClient.getQueryData<ListingDocument>(["listing", convId]);
    if (checkListingCopy(latestMessages, latestListing)) return true;
    const listingState = queryClient.getQueryState(["listing", convId]);
    if (latestListing === undefined && listingState) {
      if (listingState.status === "error") return true;
      if (listingState.fetchStatus === "fetching" && api.listings?.get) {
        try {
          latestListing = await queryClient.ensureQueryData({
            queryKey: ["listing", convId],
            queryFn: ({ signal }) => api.listings.get(convId, { signal }),
          });
        } catch {
          return true;
        }
      }
    }
    return hasListingJsonRef.current || checkListingCopy(
      queryClient.getQueryData<Message[]>(["messages", convId]) ?? latestMessages,
      latestListing,
    );
  }, [checkListingCopy, convId, queryClient]);

  const finishPhotoSend = useCallback(async () => {
    const live = getLive(convId);
    const pending = live.pendingPhotoSend;
    if (!pending || pending.running) return;
    const chatRunning = () => live.streaming || live.generating || live.resetting
      || Boolean(queryClient.getQueryData<ConversationActivity>(["activity", convId])?.busy);
    if (chatRunning() || photosUploading(convId)) {
      pending.deferred = true;
      return;
    }

    pending.running = true;
    try {
      if (pending.text) {
        if (await sendQueuedMessage(pending.text)
          && live.pendingPhotoSend === pending) {
          live.pendingPhotoSend = null;
        }
        return;
      }

      while (live.pendingPhotoSend === pending) {
        const lastChatEnd = live.lastWorkEndedAtMs;
        // A completed reply can save copy before its query refresh finishes.
        if (pending.deferred) {
          try {
            await Promise.all(["messages", "listing"].map((key) =>
              queryClient.refetchQueries({ queryKey: [key, convId], type: "active" }, {
                cancelRefetch: false, throwOnError: true,
              })));
          } catch {
            // If we cannot confirm the draft is empty, leave any saved copy alone.
            if (live.pendingPhotoSend === pending) live.pendingPhotoSend = null;
            return;
          }
        }
        const hasCopy = await hasCurrentListingCopy();
        if (live.pendingPhotoSend !== pending) return;
        if (chatRunning() || photosUploading(convId)) {
          pending.deferred = true;
          return;
        }
        if (live.lastWorkEndedAtMs !== lastChatEnd) {
          pending.deferred = true;
          continue;
        }
        live.pendingPhotoSend = null;
        if (!hasCopy) void handleGenerate();
      }
    } finally {
      pending.running = false;
    }
  }, [convId, handleGenerate, hasCurrentListingCopy, queryClient, sendQueuedMessage]);

  useEffect(() => {
    if (!busy && !serverBusy && !uploadingPhotos) void finishPhotoSend();
  }, [busy, serverBusy, uploadingPhotos, finishPhotoSend]);

  const handleSend = useCallback((alternate = false) => {
    const text = input.trim();
    const attached = images.images;
    if (editingQueuedId) {
      if (!text) return;
      editDraftKey.current = null;
      if (!editChatMessage(queueKey, editingQueuedId, text)) {
        setEditingQueuedId(null);
        addToast({ type: "warning", title: "This queued message is no longer available. Your edit is in the composer." });
        return;
      }
      setEditingQueuedId(null);
      setInput("");
      return;
    }
    if ((!text && !attached.length) || preparingDrafts.has(draftKey)) return;
    const prepareAttachments = async () => {
      preparingDrafts.add(draftKey);
      try {
        return await images.prepare(attached);
      } finally {
        preparingDrafts.delete(draftKey);
      }
    };
    const listingPhotoBatch = getComposerDraft(draftKey).listingPhotoBatch;
    if (listingPhotoBatch === "uploading") return;
    // sendMessage refuses while background work runs, so hold the message
    // instead of dropping it, or steer it into the reply being written.
    // A queued message checking the server goes first; this one waits behind it.
    if (busy || serverBusy || queueSending) {
      // Decided when the message is ready: an image upload can outlast the reply.
      const hold = (message: ReturnType<typeof newQueuedChatMessage>) => {
        const live = getLive(convId);
        const running = live.streaming || live.generating || live.resetting;
        const action = resolveFollowUp({ busy: running, canSteer: steerable && canSteerLive(convId), alternate });
        if (action === "steer") void steer(message);
        else enqueue(message);
        pinChatToBottom();
      };
      if (!attached.length) {
        setComposerListingPhotoBatch(draftKey, undefined);
        hold(newQueuedChatMessage(text));
        setInput("");
        return;
      }
      // Images on a draft with no photos become its listing photos; that waits for the run.
      if (decideComposerImageRoute(photos?.length, listingPhotoBatch) !== "chat_attachments") {
        images.setError("Images go with the next message once the current run finishes.");
        return;
      }
      void (async () => {
        const uploads = await prepareAttachments();
        if (uploads === null) return;
        images.clear(attached.map((image) => image.id));
        setInput((current) => (current.trim() === text ? "" : current));
        hold(newQueuedChatMessage(text, { payload: { attachments: uploads } }));
      })();
      return;
    }
    // When a draft has no listing photos yet, images attached in chat become
    // its listing photos. Wait for the photos query before choosing a route,
    // and keep listing-photo intent when a partial upload adds photos.
    if (attached.length > 0) {
      const route = decideComposerImageRoute(photos?.length, listingPhotoBatch);
      if (route === "busy") return;
      if (route === "wait") {
        if (queryClient.getQueryState(["photos", convId])?.fetchStatus !== "fetching") {
          void queryClient.refetchQueries({ queryKey: ["photos", convId], type: "active" });
        }
        images.setError("Still loading this item’s photos — try again in a moment.");
        return;
      }
      if (route === "listing_photos") {
        const live = getLive(convId);
        if (uploadingPhotos || photosUploading(convId) || live.pendingPhotoSend) return;
        const lastChatEnd = live.lastWorkEndedAtMs;
        setComposerListingPhotoBatch(draftKey, "uploading");
        void uploadComposerDraftPhotos(attached, {
          uploadPhoto: (file) => api.conversations.uploadListingPhoto(convId, file),
          invalidatePhotos: async () => {
            await queryClient.invalidateQueries({ queryKey: ["photos", convId] });
            void queryClient.invalidateQueries({ queryKey: ["conversations"] });
          },
          clearImages: images.clear,
          setError: images.setError,
        }).then((complete) => {
          if (getComposerDraft(draftKey).listingPhotoBatch === "uploading") {
            setComposerListingPhotoBatch(draftKey, complete ? undefined : "retry");
            if (complete) {
              live.pendingPhotoSend = {
                text, running: false, deferred: live.lastWorkEndedAtMs !== lastChatEnd,
              };
              if (text) setInput((current) => current === input ? "" : current);
              void finishPhotoSend();
            }
          }
        });
        return;
      }
    } else {
      setComposerListingPhotoBatch(draftKey, undefined);
    }
    void (async () => {
      const uploads = attached.length ? await prepareAttachments() : [];
      if (uploads === null) return;
      images.clear(attached.map((image) => image.id));
      void sendMessage(text, uploads.length ? { attachments: uploads } : undefined);
    })();
  }, [
    busy,
    serverBusy,
    queueSending,
    input,
    setInput,
    sendMessage,
    finishPhotoSend,
    pinChatToBottom,
    enqueue,
    steer,
    steerable,
    editingQueuedId,
    setEditingQueuedId,
    queueKey,
    images,
    photos,
    draftKey,
    uploadingPhotos,
    convId,
    queryClient,
  ]);
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
  const composerPlaceholder = editingQueuedId
    ? "Edit the queued message…"
    : busy && canSteer
    ? `Redirect this reply, or ${modifierKeyLabel()}+Enter to queue…`
    : busy
    ? "Queue a follow-up…"
    : !hasPhotos
    ? "Upload photos to begin"
    : awaitingSellerAnswers
      ? "Answer the questions above..."
      : hasMessages || hasListingJson
        ? "Refine the listing..."
        : "Add a note, or generate the listing...";

  useGenerateRecovery(convId, generating, hasListingJson, streamFromFetch);

  const messageCount = messages?.length ?? 0;
  const loaded = messages !== undefined;
  const empty = loaded && !hasMessages && !busy && !streamFailed;
  const statusLabel = busy ? localLabel || activityDetail : "";
  useEffect(() => {
    if (loaded) onStatusChange?.({ convId, busy, label: statusLabel, messageCount, empty });
  }, [onStatusChange, convId, loaded, busy, statusLabel, messageCount, empty]);

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
      <ChatTranscript scroll={scroll} busy={busy} doneMessage={generating ? "Listing generation finished." : "Reply finished."}>
        {isLoading && !hasMessages ? (
          <div className="flex justify-center py-4"><Spinner size="md" tone="muted" /></div>
        ) : null}

        {!isLoading && !hasMessages && !busy && !streamFailed && hasListingJson ? (
          // The listing already has copy: Generate would overwrite it, so chat
          // invites questions and edits, and Regenerate asks before it rewrites.
          <Empty className="py-8">
            <EmptyHeader>
              <EmptyTitle>Ask Ant about this listing</EmptyTitle>
              <EmptyDescription>Ask a question or describe a change, like a shorter title or a new price.</EmptyDescription>
            </EmptyHeader>
            {hasPhotos ? (
              <EmptyContent>
                <RegenerateListingButton convId={convId} />
              </EmptyContent>
            ) : null}
          </Empty>
        ) : null}

        {!isLoading && !hasMessages && !busy && !streamFailed && !hasListingJson ? (
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
                <Button type="button" onClick={handleGenerate} disabled={generating || streaming || uploadingPhotos}>
                  {uploadingPhotos ? "Uploading photos…" : "Generate listing"}
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
          <StreamingAssistantRow received={streamReceived} complete={!streaming && !generating} display={assistantDisplayText} />
        ) : null}

        <QueuedMessages
          queue={pendingQueue}
          canSendNow={canSteer && !editingQueuedId}
          onSendNow={(id) => { void sendNow(id); }}
          onEdit={startEditQueued}
          onRemove={handleRemoveQueued}
          editingId={editingQueuedId}
          onCancelEdit={cancelEditQueued}
          editBlockedReason={
            editingQueuedId ? "Finish editing the other message first"
              : input.trim() ? "Send or clear the message box first" : null
          }
          imageCount={(message) => (message.payload as { attachments?: unknown[] } | undefined)?.attachments?.length ?? 0}
        />

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
        busy={busy}
        stopping={stopping}
        onSend={handleSend}
        onStop={() => { void handleCancel(); }}
        stopLabel="Stop everything running for this listing"
        canSteer={canSteer}
        editingQueued={editingQueuedId !== null}
        onCancelEdit={cancelEditQueued}
        onSendQueuedNow={sendFirstQueuedNow}
        onEditQueued={editLastQueued}
      />
    </div>
    </ChatCitationRevealContext>
  );
}
