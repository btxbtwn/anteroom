import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ConversationActivity } from "../../api/types";
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
  sendMessage: (
    text: string,
    opts?: { browserJobId?: string; browserFields?: QueuedChatMessage["browserFields"] },
  ) => Promise<boolean>;
  queuedMessage: string | null | undefined;
  onQueuedMessageConsumed: (() => void) | undefined;
  pinChatToBottom: () => void;
}

/** Follow-ups held client-side while a run is going, sent one at a time when it settles. */
export function useChatQueue({
  convId,
  busy,
  sendMessage,
  queuedMessage,
  onQueuedMessageConsumed,
  pinChatToBottom,
}: Options) {
  // Follow-ups typed while a run is still writing — same idea as T3 Code's
  // composer queue: hold them client-side and send when the turn settles.
  const [pendingQueue, setPendingQueue] = useState<QueuedChatMessage[]>([]);
  const drainLockRef = useRef(false);
  const queryClient = useQueryClient();

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

  const enqueue = useCallback((message: QueuedChatMessage) => {
    setPendingQueue((queue) => enqueueChatMessage(queue, message));
  }, []);

  const handleRemoveQueued = useCallback((id: string) => {
    setPendingQueue((queue) => removeChatMessage(queue, id));
  }, []);

  return { pendingQueue, enqueue, handleRemoveQueued };
}
