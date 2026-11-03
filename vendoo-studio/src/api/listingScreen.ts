import { currentHostedSession } from "./transport";

// Coalesce core reads issued together. Share only requests currently running. React Query owns freshness and invalidation.
interface ReadBatch { keys: Set<string>; promise: Promise<Record<string, unknown>> }
const pending = new Map<string, ReadBatch>();
let owner = currentHostedSession();

export async function listingScreenRead<T>(path: string, read: <R>(path: string) => Promise<R>): Promise<T> {
  const session = currentHostedSession();
  if (session !== owner) { pending.clear(); owner = session; }
  if (!session) return read<T>(path);
  const match = /^\/conversations\/([^/?]+)(?:\/(listing|photos|messages|activity))?$/.exec(path);
  const job = /^\/jobs\?conversation_id=([^&]+)$/.exec(path);
  if (!match && !job) return read<T>(path);
  const id = match ? match[1] : decodeURIComponent(job![1]);
  const key = match ? match[2] || "conversation" : "jobs";
  let batch = pending.get(id);
  if (!batch) {
    const keys = new Set<string>();
    const promise = Promise.resolve().then(async () => {
      if (currentHostedSession() !== session) throw new Error("Your account changed.");
      // A single polling read should not reload the whole screen.
      if (keys.size === 1) {
        if (pending.get(id) === started) pending.delete(id);
        return { [key]: await read<unknown>(path) };
      }
      return read<Record<string, unknown>>(`/conversations/${id}/screen`);
    });
    const started = { keys, promise };
    pending.set(id, batch = started);
    void promise.finally(() => { if (pending.get(id) === started) pending.delete(id); }).catch(() => {});
  }
  batch.keys.add(key);
  const result = await batch.promise;
  if (currentHostedSession() !== session) throw new Error("Your account changed.");
  return result[key] as T;
}
