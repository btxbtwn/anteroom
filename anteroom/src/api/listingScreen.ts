import { isBackgroundSignal, promoteBackgroundSignal, registerBackgroundSignal } from "./requestScheduler";
import { currentHostedSession } from "./transport";

// Coalesce core reads issued together. Share only requests currently running. React Query owns freshness and invalidation.
// A shared request is aborted only once every caller that joined it has aborted; one listing closing never cancels another's read.
interface ReadBatch { keys: Set<string>; promise: Promise<Record<string, unknown>>; controller: AbortController; waiting: number }
const pending = new Map<string, ReadBatch>();
let owner = currentHostedSession();

export type ScreenRead = <R>(path: string, signal?: AbortSignal) => Promise<R>;

const CONVERSATION_PART_KEYS: Record<string, string> = {
  listing: "listing",
  photos: "photos",
  messages: "messages",
  activity: "activity",
  "marketplace-listings": "marketplace_listings",
  "native-marketplaces": "native_marketplaces",
};

function matchScreenTarget(path: string): { id: string; key: string } | null {
  const conv = /^\/conversations\/([^/?]+)(?:\/(listing|photos|messages|activity|marketplace-listings|native-marketplaces))?$/.exec(path);
  if (conv) {
    return { id: conv[1], key: conv[2] ? CONVERSATION_PART_KEYS[conv[2]] : "conversation" };
  }
  const job = /^\/jobs\?conversation_id=([^&]+)$/.exec(path);
  if (job) {
    return { id: decodeURIComponent(job[1]), key: "jobs" };
  }
  if (path.startsWith("/marketplace-sales?")) {
    const params = new URLSearchParams(path.slice("/marketplace-sales?".length));
    const convId = params.get("conversation_id");
    const state = params.get("state") ?? "new";
    if (convId && state === "new") {
      // Only coalesce when no extra unrecognized filter parameters are present.
      const allowed = [...params.keys()].every((name) => name === "conversation_id" || name === "state");
      if (allowed) return { id: convId, key: "marketplace_sales" };
    }
  }
  return null;
}

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
  const target = matchScreenTarget(path);
  if (!target) return read<T>(path, signal);
  const { id, key } = target;
  const callerIsBackground = isBackgroundSignal(signal);
  let batch = pending.get(id);
  if (!batch) {
    const keys = new Set<string>();
    const controller = new AbortController();
    if (callerIsBackground) registerBackgroundSignal(controller.signal);
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
  } else if (!callerIsBackground && isBackgroundSignal(batch.controller.signal)) {
    promoteBackgroundSignal(batch.controller.signal);
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
  if (!(key in result)) return read<T>(path, signal);
  return result[key] as T;
}
