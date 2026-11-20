/**
 * Prioritizes interactive user requests ahead of background prefetches and
 * thumbnail warm-ups, capping background concurrency so warm-up bursts never
 * starve clicks, saves, or the hosted database pool.
 *
 * Long-lived streams (SSE) and relay long-polls are excluded from the
 * interactive count so an open stream never blocks background work forever.
 */

export const MAX_BACKGROUND_CONCURRENT = 2;

const backgroundSignals = new WeakSet<AbortSignal>();
let backgroundContextDepth = 0;

let activeInteractive = 0;
let activeBackground = 0;

interface BackgroundWaiter {
  signal: AbortSignal | undefined;
  admit: (asInteractive: boolean) => void;
  cleanup: () => void;
}

const backgroundQueue: BackgroundWaiter[] = [];

/** Marks an AbortSignal as belonging to a background prefetch or warm-up. */
export function registerBackgroundSignal(signal: AbortSignal): AbortSignal {
  backgroundSignals.add(signal);
  return signal;
}

/** True when the given AbortSignal was marked as a background request. */
export function isBackgroundSignal(signal?: AbortSignal | null): boolean {
  return Boolean(signal && backgroundSignals.has(signal));
}

/**
 * Runs `fn` inside a synchronous background context so any `studioFetch`
 * initiated before the first `await` is scheduled on the background lane.
 */
export function runInBackgroundContext<T>(fn: () => T): T {
  backgroundContextDepth += 1;
  try {
    return fn();
  } finally {
    backgroundContextDepth -= 1;
  }
}

/**
 * Promotes a queued background signal to interactive priority when a real
 * screen mounts and shares its in-flight request (e.g. `/screen` or a photo).
 */
export function promoteBackgroundSignal(signal: AbortSignal): void {
  backgroundSignals.delete(signal);
  const index = backgroundQueue.findIndex((waiter) => waiter.signal === signal);
  if (index === -1) return;
  const [waiter] = backgroundQueue.splice(index, 1);
  waiter.cleanup();
  activeInteractive += 1;
  waiter.admit(true);
}

function hasEventStreamAccept(headers?: HeadersInit): boolean {
  if (!headers) return false;
  if (headers instanceof Headers) {
    return (headers.get("accept") ?? "").toLowerCase().includes("text/event-stream");
  }
  if (Array.isArray(headers)) {
    return headers.some(([k, v]) => k.toLowerCase() === "accept" && v.toLowerCase().includes("text/event-stream"));
  }
  for (const [k, v] of Object.entries(headers)) {
    if (k.toLowerCase() === "accept" && typeof v === "string" && v.toLowerCase().includes("text/event-stream")) {
      return true;
    }
  }
  return false;
}

function isLongLivedRequest(input: string, options?: RequestInit): boolean {
  if (hasEventStreamAccept(options?.headers)) return true;
  return input.includes("/api/browser-relay/next") || input.includes("/api/mac-inference/");
}

function drainBackgroundQueue(): void {
  while (activeInteractive === 0 && activeBackground < MAX_BACKGROUND_CONCURRENT && backgroundQueue.length > 0) {
    const waiter = backgroundQueue.shift()!;
    waiter.cleanup();
    if (waiter.signal?.aborted) continue;
    activeBackground += 1;
    waiter.admit(false);
  }
}

function acquireBackgroundSlot(signal?: AbortSignal): Promise<"interactive" | "background"> {
  signal?.throwIfAborted();
  if (activeInteractive === 0 && activeBackground < MAX_BACKGROUND_CONCURRENT) {
    activeBackground += 1;
    return Promise.resolve("background");
  }
  return new Promise<"interactive" | "background">((resolve, reject) => {
    const onAbort = () => {
      const index = backgroundQueue.indexOf(waiter);
      if (index !== -1) backgroundQueue.splice(index, 1);
      reject(signal!.reason);
    };
    const cleanup = () => {
      signal?.removeEventListener("abort", onAbort);
    };
    const waiter: BackgroundWaiter = {
      signal,
      admit: (asInteractive) => resolve(asInteractive ? "interactive" : "background"),
      cleanup,
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    backgroundQueue.push(waiter);
  });
}

/**
 * Schedules a `studioFetch` execution on the interactive or background lane.
 * Interactive requests start synchronously so caller timing is unchanged.
 */
export function scheduleRequest<T>(
  input: string,
  options: RequestInit | undefined,
  run: () => Promise<T>,
): Promise<T> {
  if (isLongLivedRequest(input, options)) return run();

  const signal = options?.signal ?? undefined;
  const isBackground = backgroundContextDepth > 0 || isBackgroundSignal(signal);

  if (!isBackground) {
    activeInteractive += 1;
    let promise: Promise<T>;
    try {
      promise = run();
    } catch (error) {
      activeInteractive -= 1;
      drainBackgroundQueue();
      throw error;
    }
    return promise.finally(() => {
      activeInteractive -= 1;
      drainBackgroundQueue();
    });
  }

  return acquireBackgroundSlot(signal).then((lane) => {
    const release = () => {
      if (lane === "interactive") activeInteractive -= 1;
      else activeBackground -= 1;
      drainBackgroundQueue();
    };
    let promise: Promise<T>;
    try {
      promise = run();
    } catch (error) {
      release();
      throw error;
    }
    return promise.finally(release);
  });
}
