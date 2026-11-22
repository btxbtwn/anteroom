/**
 * Follow-ups held while a chat is busy, kept outside any one panel the way
 * T3 Code keeps queued runs on the thread rather than in the composer: they
 * survive switching listings, closing the chat popup and reloading the page.
 *
 * Text is saved in this browser and shared with its other tabs. Images live
 * in memory only, because their prepared uploads are too big for browser
 * storage, so a reload drops a queued message that carries images rather
 * than sending it without them. So does a message asked before its
 * assistant conversation existed, which has nowhere to go after a reload.
 * A saved message older than a day is dropped rather than sent out of the
 * blue the next time its listing opens.
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

/** Kept only in this tab's memory: never written, and kept when storage is read again. */
function memoryOnly(item: QueuedChatMessage): boolean {
  return item.payload !== undefined || item.threadId === null;
}

function readStorage(): Queues {
  const read: Queues = {};
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object") return read;
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
      if (kept.length) read[key] = kept;
    }
  } catch {
    // Storage blocked or unreadable: the queue still works for this session.
  }
  return read;
}

function load(): Queues {
  if (!queues) queues = readStorage();
  return queues;
}

/**
 * Take what another tab saved, keeping this tab's memory-only messages in
 * place, so a message one tab sent or removed is not sent again from here.
 */
function syncFromStorage(): string[] {
  const before = load();
  const saved = readStorage();
  const next: Queues = {};
  for (const key of new Set([...Object.keys(before), ...Object.keys(saved)])) {
    const durable = saved[key] ?? [];
    const mine = before[key] ?? [];
    // Keep this tab's order and its memory-only items; follow storage for the rest.
    const durableIds = new Set(durable.map((item) => item.id));
    const merged = mine.filter((item) => memoryOnly(item) || durableIds.has(item.id))
      .map((item) => (memoryOnly(item) ? item : { ...item, ...durable.find((d) => d.id === item.id)! }));
    const known = new Set(merged.map((item) => item.id));
    merged.push(...durable.filter((item) => !known.has(item.id)));
    if (merged.length) next[key] = merged;
  }
  queues = next;
  return [...new Set([...Object.keys(before), ...Object.keys(next)])];
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return;
    for (const key of syncFromStorage()) listeners.get(key)?.forEach((listener) => listener());
  });
}

function persist() {
  const all = load();
  const saved: Queues = {};
  for (const [key, items] of Object.entries(all)) {
    const durable = items
      .filter((item) => !memoryOnly(item))
      .map(({ id, text, createdAt, threadId }) => ({ id, text, createdAt, ...(threadId !== undefined ? { threadId } : {}) }));
    if (durable.length) saved[key] = durable;
  }
  try {
    if (Object.keys(saved).length) globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(saved));
    else globalThis.localStorage?.removeItem(STORAGE_KEY);
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
  write(key, chatQueue(key).map((message) => (message.id === id ? { ...message, text } : message)));
}

/**
 * Move a queued message to the front and mark it to steer: it goes as soon as
 * the answer being written has been cut off, ahead of everything else.
 */
export function promoteChatMessage(key: string, id: string) {
  const queue = chatQueue(key);
  const message = queue.find((item) => item.id === id);
  if (!message) return;
  write(key, [{ ...message, steer: true }, ...queue.filter((item) => item.id !== id)]);
}

/** Put a new message first, marked to steer. */
export function steerChatMessage(key: string, message: QueuedChatMessage) {
  write(key, [{ ...message, steer: true }, ...chatQueue(key)]);
}

/** The oldest message that `matches` leaves the queue; the rest stay in order. */
export function takeNextChatMessage<P = unknown>(
  key: string,
  matches: (message: QueuedChatMessage<P>) => boolean = () => true,
): QueuedChatMessage<P> | null {
  // Another tab may have sent it a moment ago.
  syncFromStorage();
  const queue = chatQueue<P>(key);
  const next = queue.find(matches) ?? null;
  if (next) write(key, queue.filter((message) => message !== next) as QueuedChatMessage[]);
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

/** Tests only: forget everything, including what storage held at first read. */
export function resetChatQueuesForTest() {
  queues = null;
  listeners.clear();
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
