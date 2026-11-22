import { useCallback, useEffect, useSyncExternalStore } from "react";
import type { ChatAttachmentUpload } from "../../api/types";
import {
  enqueueChatMessage,
  newQueuedChatMessage,
  promoteChatMessage,
  removeChatMessage,
  returnChatMessage,
  steerChatMessage,
  takeNextChatMessage,
  useChatQueueItems,
  type QueuedChatMessage,
} from "./chatQueueStore";
import { getLive } from "./liveChatStore";

/** Images a queued listing follow-up carries, already prepared for upload. */
export type ListingQueuePayload = { attachments: ChatAttachmentUpload[] };

/** Listings sending a queued message right now; one at a time per listing, any number across them. */
const draining = new Set<string>();
const drainListeners = new Set<() => void>();
function subscribeDrain(listener: () => void) {
  drainListeners.add(listener);
  return () => { drainListeners.delete(listener); };
}
function notifyDrain() {
  for (const listener of drainListeners) listener();
}

export function listingQueueKey(convId: string) {
  return `listing:${convId}`;
}

interface Options {
  convId: string;
  busy: boolean;
  /** Background listing work (a field fill after a save). A held follow-up waits for it. */
  serverBusy: boolean;
  /** Hold the queue while one of its messages is being edited. */
  paused?: boolean;
  sendMessage: (text: string, attachments: ChatAttachmentUpload[]) => Promise<boolean>;
  /** Cut off the reply being written; false when nothing can take a steer. */
  interruptAnswer: () => Promise<boolean>;
  queuedMessage: string | null | undefined;
  onQueuedMessageConsumed: (() => void) | undefined;
  pinChatToBottom: () => void;
}

/**
 * Follow-ups held while a run is going, sent one at a time when it settles.
 * They live in the shared queue store, so they outlast this panel.
 */
export function useChatQueue({
  convId,
  busy,
  serverBusy,
  paused = false,
  sendMessage,
  interruptAnswer,
  queuedMessage,
  onQueuedMessageConsumed,
  pinChatToBottom,
}: Options) {
  const key = listingQueueKey(convId);
  const sending = useSyncExternalStore(subscribeDrain, () => draining.has(key), () => false);
  const pendingQueue = useChatQueueItems<ListingQueuePayload>(key);

  // Ask-chat prompts from the inspector join the same queue as typed follow-ups.
  useEffect(() => {
    if (!queuedMessage) return;
    const text = queuedMessage;
    onQueuedMessageConsumed?.();
    enqueueChatMessage(key, newQueuedChatMessage(text));
    pinChatToBottom();
  }, [key, queuedMessage, onQueuedMessageConsumed, pinChatToBottom]);

  // serverBusy is a dependency, not a cache read: the fill a chat save starts
  // ends after the reply does, and the held follow-up must send when it ends.
  useEffect(() => {
    if (sending || busy || serverBusy || paused || pendingQueue.length === 0 || draining.has(key)) return;
    const liveState = getLive(convId);
    if (liveState.streaming || liveState.generating || liveState.resetting) return;
    const next = takeNextChatMessage<ListingQueuePayload>(key);
    if (!next) return;
    draining.add(key);
    notifyDrain();
    void sendMessage(next.text, next.payload?.attachments ?? []).then((started) => {
      if (!started) returnChatMessage(key, next);
    }).finally(() => {
      draining.delete(key);
      notifyDrain();
    });
  }, [sending, busy, serverBusy, paused, pendingQueue, sendMessage, convId, key]);

  const enqueue = useCallback((message: QueuedChatMessage) => enqueueChatMessage(key, message), [key]);

  /** Send this follow-up next, cutting off the reply being written when it can take a steer. */
  const steer = useCallback(async (message: QueuedChatMessage) => {
    steerChatMessage(key, message);
    pinChatToBottom();
    await interruptAnswer();
  }, [key, interruptAnswer, pinChatToBottom]);

  const sendNow = useCallback(async (id: string) => {
    promoteChatMessage(key, id);
    await interruptAnswer();
  }, [key, interruptAnswer]);

  const handleRemoveQueued = useCallback((id: string) => removeChatMessage(key, id), [key]);

  /** True while a queued message is on its way, its server check included; a new message waits behind it. */
  return { key, pendingQueue, sending, enqueue, steer, sendNow, handleRemoveQueued };
}
