import { useCallback, useEffect, useRef, useState } from "react";
import {
  enqueueChatMessage,
  newQueuedChatMessage,
  removeChatMessage,
  takeNextChatMessage,
  type QueuedChatMessage,
} from "../chatMessageQueue";
import { getLive } from "./liveChatStore";

interface Options {
  convId: string;
  busy: boolean;
  /** Background listing work (a field fill after a save). A held follow-up waits for it. */
  serverBusy: boolean;
  sendMessage: (text: string) => Promise<boolean>;
  queuedMessage: string | null | undefined;
  onQueuedMessageConsumed: (() => void) | undefined;
  pinChatToBottom: () => void;
}

/** Follow-ups held client-side while a run is going, sent one at a time when it settles. */
export function useChatQueue({
  convId,
  busy,
  serverBusy,
  sendMessage,
  queuedMessage,
  onQueuedMessageConsumed,
  pinChatToBottom,
}: Options) {
  // Follow-ups typed while a run is still writing — same idea as T3 Code's
  // composer queue: hold them client-side and send when the turn settles.
  const [pendingQueue, setPendingQueue] = useState<QueuedChatMessage[]>([]);
  const drainLockRef = useRef(false);

  useEffect(() => {
    setPendingQueue([]);
    drainLockRef.current = false;
  }, [convId]);

  // Ask-chat prompts from the inspector join the same queue as typed follow-ups.
  useEffect(() => {
    if (!queuedMessage) return;
    const text = queuedMessage;
    onQueuedMessageConsumed?.();
    setPendingQueue((queue) => enqueueChatMessage(queue, newQueuedChatMessage(text)));
    pinChatToBottom();
  }, [queuedMessage, onQueuedMessageConsumed, pinChatToBottom]);

  // serverBusy is a dependency, not a cache read: the fill a chat save starts
  // ends after the reply does, and the held follow-up must send when it ends.
  useEffect(() => {
    if (busy || serverBusy || pendingQueue.length === 0 || drainLockRef.current) return;
    const liveState = getLive(convId);
    if (liveState.streaming || liveState.generating || liveState.resetting) return;
    const { next, rest } = takeNextChatMessage(pendingQueue);
    if (!next) return;
    drainLockRef.current = true;
    setPendingQueue(rest);
    void sendMessage(next.text).then((started) => {
      if (!started) {
        setPendingQueue((queue) => [next, ...queue]);
      }
    }).finally(() => {
      drainLockRef.current = false;
    });
  }, [busy, serverBusy, pendingQueue, sendMessage, convId]);

  const enqueue = useCallback((message: QueuedChatMessage) => {
    setPendingQueue((queue) => enqueueChatMessage(queue, message));
  }, []);

  const handleRemoveQueued = useCallback((id: string) => {
    setPendingQueue((queue) => removeChatMessage(queue, id));
  }, []);

  return { pendingQueue, enqueue, handleRemoveQueued };
}
