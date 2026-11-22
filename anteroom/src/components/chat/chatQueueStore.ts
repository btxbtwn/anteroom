/**
 * Follow-ups held while a chat is busy, kept outside any one panel the way
 * T3 Code keeps queued runs on the thread rather than in the composer: they
 * survive switching listings, closing the chat popup and reloading the page.
 *
 * Each tab keeps its own queue (sessionStorage), because only the tab that
 * queued a message knows whether the reply it waits for is still being
 * written; another tab, seeing the listing idle, would send it at once.
 * Closing the tab ends its queue, and only a reload picks a saved one back
 * up (a duplicated tab gets a copy of sessionStorage, but starts empty). Images live in memory only, because their
 * prepared uploads are too big for browser storage, so a reload drops a
 * queued message that carries images rather than sending it without them.
 * So does a message asked before its assistant conversation existed, which
 * has nowhere to go after a reload. A saved message older than a day is
 * dropped rather than sent out of the blue.
 */
import { useSyncExternalStore } from "react";

export type QueuedChatMessage<P = unknown> = {
  id: string;
  text: string;
  createdAt: string;
  /** The conversation it belongs to, when one queue serves several (the assistant). */
  threadId?: string | null;
  /** Sent ahead of the rest as soon as the interrupted answer has stopped. */
  steer?: boolean;
  /** Images and other in-memory extras; never written to browser storage. */
  payload?: P;
};

const STORAGE_KEY = "anteroom.chatQueue.v1";
const SAVED_FOR_MS = 24 * 60 * 60 * 1000;

type Queues = Record<string, QueuedChatMessage[]>;

let queues: Queues | null = null;
const listeners = new Map<string, Set<() => void>>();
const EMPTY: readonly QueuedChatMessage[] = [];

/** Kept only in memory: never written to storage. */
function memoryOnly(item: QueuedChatMessage): boolean {
  return item.payload !== undefined || item.threadId === null;
}

function storage(): Storage | undefined {
  try {
    return globalThis.sessionStorage;
  } catch {
    return undefined;
  }
}

/**
 * Whether this page is a reload. Chrome copies sessionStorage into a
 * duplicated tab, which would send the same queue from two tabs, so only a
 * reload picks a saved queue back up.
 */
function reloaded(): boolean {
  try {
    const entry = globalThis.performance?.getEntriesByType?.("navigation")[0] as PerformanceNavigationTiming | undefined;
    return entry?.type === "reload";
  } catch {
    return false;
  }
}

let restoreSaved = reloaded;

function load(): Queues {
  if (queues) return queues;
  queues = {};
  if (!restoreSaved()) return queues;
  try {
    const raw = storage()?.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object") {
      const oldest = Date.now() - SAVED_FOR_MS;
      for (const [key, items] of Object.entries(parsed as Record<string, unknown>)) {
        if (!Array.isArray(items)) continue;
        const kept = items
          .filter(
            (item): item is QueuedChatMessage =>
              Boolean(item) && typeof item.id === "string" && typeof item.text === "string"
              && typeof item.createdAt === "string" && Date.parse(item.createdAt) >= oldest,
          )
          .map(({ id, text, createdAt, threadId }) => ({
            id,
            text,
            createdAt,
            ...(typeof threadId === "string" ? { threadId } : {}),
          }));
        if (kept.length) queues[key] = kept;
      }
    }
  } catch {
    // Storage blocked or unreadable: the queue still works until the page reloads.
  }
  return queues;
}

function persist() {
  const saved: Queues = {};
  for (const [key, items] of Object.entries(load())) {
    const durable = items
      .filter((item) => !memoryOnly(item))
      .map(({ id, text, createdAt, threadId }) => ({ id, text, createdAt, ...(threadId !== undefined ? { threadId } : {}) }));
    if (durable.length) saved[key] = durable;
  }
  try {
    if (Object.keys(saved).length) storage()?.setItem(STORAGE_KEY, JSON.stringify(saved));
    else storage()?.removeItem(STORAGE_KEY);
  } catch {
    // Quota or private mode: keep the in-memory queue.
  }
}

function write(key: string, next: QueuedChatMessage[]) {
  const all = load();
  if (next.length) all[key] = next;
  else delete all[key];
  persist();
  listeners.get(key)?.forEach((listener) => listener());
}

export function newQueuedChatMessage<P = unknown>(
  text: string,
  extra: Partial<Pick<QueuedChatMessage<P>, "threadId" | "payload" | "steer">> = {},
): QueuedChatMessage<P> {
  return { id: crypto.randomUUID(), text, createdAt: new Date().toISOString(), ...extra };
}

export function chatQueue<P = unknown>(key: string): readonly QueuedChatMessage<P>[] {
  return (load()[key] ?? EMPTY) as readonly QueuedChatMessage<P>[];
}

export function subscribeChatQueue(key: string, listener: () => void): () => void {
  let set = listeners.get(key);
  if (!set) listeners.set(key, (set = new Set()));
  set.add(listener);
  return () => set.delete(listener);
}

export function useChatQueueItems<P = unknown>(key: string): readonly QueuedChatMessage<P>[] {
  return useSyncExternalStore(
    (listener) => subscribeChatQueue(key, listener),
    () => chatQueue<P>(key),
    () => chatQueue<P>(key),
  );
}

export function enqueueChatMessage(key: string, message: QueuedChatMessage) {
  write(key, [...chatQueue(key), message]);
}

export function removeChatMessage(key: string, id: string) {
  const queue = chatQueue(key);
  if (queue.some((message) => message.id === id)) write(key, queue.filter((message) => message.id !== id));
}

export function editChatMessage(key: string, id: string, text: string) {
  const items = chatQueue(key);
  if (!items.some((message) => message.id === id)) return false;
  write(key, items.map((message) => (message.id === id ? { ...message, text } : message)));
  return true;
}

/**
 * Move a queued message to the front and mark it to steer: it goes as soon as
 * the answer being written has been cut off, ahead of everything else.
 */
export function promoteChatMessage(key: string, id: string) {
  const queue = chatQueue(key);
  const message = queue.find((item) => item.id === id);
  if (!message) return;
  write(key, behindSteers(queue.filter((item) => item.id !== id), message));
}

/** Put a new message first, marked to steer. */
export function steerChatMessage(key: string, message: QueuedChatMessage) {
  write(key, behindSteers(chatQueue(key), message));
}

/** Ahead of everything waiting, but behind steers already on their way, which go in the order they came. */
function behindSteers(queue: readonly QueuedChatMessage[], message: QueuedChatMessage): QueuedChatMessage[] {
  const steering = queue.findIndex((item) => !item.steer);
  const at = steering < 0 ? queue.length : steering;
  return [...queue.slice(0, at), { ...message, steer: true }, ...queue.slice(at)];
}

/** The oldest message that `matches` leaves the queue; the rest stay in order. */
export function takeNextChatMessage<P = unknown>(
  key: string,
  matches: (message: QueuedChatMessage<P>) => boolean = () => true,
): QueuedChatMessage<P> | null {
  const oldest = Date.now() - SAVED_FOR_MS;
  const all = chatQueue<P>(key);
  // A page left open for days must not send a message from long ago either.
  const queue = all.filter((message) => Date.parse(message.createdAt) >= oldest);
  const next = queue.find(matches) ?? null;
  if (next || queue.length !== all.length) {
    write(key, queue.filter((message) => message !== next) as QueuedChatMessage[]);
  }
  return next;
}

/** Messages asked before their conversation existed now belong to it. */
export function assignChatThread(key: string, threadId: string) {
  const queue = chatQueue(key);
  if (queue.some((message) => message.threadId === null)) {
    write(key, queue.map((message) => (message.threadId === null ? { ...message, threadId } : message)));
  }
}

/** A send that did not start goes back to the front. */
export function returnChatMessage(key: string, message: QueuedChatMessage) {
  write(key, [message, ...chatQueue(key).filter((item) => item.id !== message.id)]);
}

/** Tests only: forget everything; the next read picks up what storage holds, as a reload does. */
export function resetChatQueuesForTest({ reload = true }: { reload?: boolean } = {}) {
  queues = null;
  listeners.clear();
  restoreSaved = () => reload;
}

/**
 * What submitting does while a run is going, as T3 Code's
 * resolveComposerDispatchMode: steer by default, the modifier does the
 * opposite, and a run that cannot take a steer always queues.
 */
export type FollowUpAction = "send" | "steer" | "queue";

export function resolveFollowUp(input: { busy: boolean; canSteer: boolean; alternate: boolean }): FollowUpAction {
  if (!input.busy) return "send";
  if (!input.canSteer) return "queue";
  return input.alternate ? "queue" : "steer";
}
