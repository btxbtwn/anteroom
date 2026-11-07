import { currentHostedSession } from "./transport";

// Coalesce core reads issued together. Share only requests currently running. React Query owns freshness and invalidation.
// A shared request is aborted only once every caller that joined it has aborted; one listing closing never cancels another's read.
interface ReadBatch { keys: Set<string>; promise: Promise<Record<string, unknown>>; controller: AbortController; waiting: number }
const pending = new Map<string, ReadBatch>();
let owner = currentHostedSession();

export type ScreenRead = <R>(path: string, signal?: AbortSignal) => Promise<R>;

/** Settles with `promise`, or rejects as soon as this caller's own signal aborts. */
function untilAborted<T>(promise: Promise<T>, signal: AbortSignal | undefined, onAbort?: () => void): Promise<T> {
  if (!signal) return promise;
  return new Promise<T>((resolve, reject) => {
    const abort = () => { onAbort?.(); reject(signal.reason); };
    signal.addEventListener("abort", abort, { once: true });
    promise.then(
      (value) => { signal.removeEventListener("abort", abort); resolve(value); },
      (error: unknown) => { signal.removeEventListener("abort", abort); reject(error); },
    );
  });
}

export async function listingScreenRead<T>(path: string, read: ScreenRead, signal?: AbortSignal): Promise<T> {
  signal?.throwIfAborted();
  const session = currentHostedSession();
  if (session !== owner) { pending.clear(); owner = session; }
  if (!session) return read<T>(path, signal);
  const match = /^\/conversations\/([^/?]+)(?:\/(listing|photos|messages|activity))?$/.exec(path);
  const job = /^\/jobs\?conversation_id=([^&]+)$/.exec(path);
  if (!match && !job) return read<T>(path, signal);
  const id = match ? match[1] : decodeURIComponent(job![1]);
  const key = match ? match[2] || "conversation" : "jobs";
  let batch = pending.get(id);
  if (!batch) {
    const keys = new Set<string>();
    const controller = new AbortController();
    const promise = Promise.resolve().then(async () => {
      if (currentHostedSession() !== session) throw new Error("Your account changed.");
      // A single polling read should not reload the whole screen.
      if (keys.size === 1) {
        if (pending.get(id) === started) pending.delete(id);
        return { [key]: await read<unknown>(path, controller.signal) };
      }
      return read<Record<string, unknown>>(`/conversations/${id}/screen`, controller.signal);
    });
    const started: ReadBatch = { keys, promise, controller, waiting: 0 };
    pending.set(id, batch = started);
    void promise.finally(() => { if (pending.get(id) === started) pending.delete(id); }).catch(() => {});
  }
  const joined = batch;
  joined.keys.add(key);
  // A caller without a signal can never leave, so the shared read always finishes for it.
  joined.waiting += signal ? 1 : Infinity;
  const result = await untilAborted(joined.promise, signal, () => {
    joined.waiting -= 1;
    if (joined.waiting > 0) return;
    if (pending.get(id) === joined) pending.delete(id);
    joined.controller.abort();
  });
  if (currentHostedSession() !== session) throw new Error("Your account changed.");
  return result[key] as T;
}
