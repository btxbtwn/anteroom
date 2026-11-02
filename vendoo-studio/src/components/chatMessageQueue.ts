/** A follow-up typed while generation/chat is still running. */
export type QueuedChatMessage = {
  id: string;
  text: string;
  createdAt: string;
};

export function newQueuedChatMessage(text: string): QueuedChatMessage {
  return {
    id: crypto.randomUUID(),
    text,
    createdAt: new Date().toISOString(),
  };
}

export function enqueueChatMessage(
  queue: readonly QueuedChatMessage[],
  message: QueuedChatMessage,
): QueuedChatMessage[] {
  return [...queue, message];
}

export function removeChatMessage(
  queue: readonly QueuedChatMessage[],
  id: string,
): QueuedChatMessage[] {
  return queue.filter((message) => message.id !== id);
}

/** Oldest message leaves; remaining stay in order. */
export function takeNextChatMessage(queue: readonly QueuedChatMessage[]): {
  next: QueuedChatMessage | null;
  rest: QueuedChatMessage[];
} {
  if (queue.length === 0) return { next: null, rest: [] };
  return { next: queue[0], rest: queue.slice(1) };
}
