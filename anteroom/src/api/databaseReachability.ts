/**
 * Whether the hosted app can reach the seller's data.
 *
 * The server marks its "Cloud data is temporarily unavailable" 503 with
 * `X-Anteroom-Reason: database_unavailable`. One such answer is only a hint:
 * the app asks `/api/ready` (no session needed), and only when that fails too
 * does it say the data is unreachable. While it does, `/api/ready` is asked
 * every 5 seconds; only a passing answer clears the state, and then the reads
 * that failed meanwhile are asked again.
 */

export const DATABASE_UNAVAILABLE_REASON = "database_unavailable";
export const READY_POLL_MS = 5000;
const READY_DEADLINE_MS = 5000;

export type Reachability = "ok" | "checking" | "unreachable";

type Probe = (origin: string) => Promise<boolean>;

async function probeReady(origin: string): Promise<boolean> {
  try {
    const response = await fetch(new URL("/api/ready", origin).href, {
      cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
      signal: AbortSignal.timeout(READY_DEADLINE_MS),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export function createReachability(probe: Probe = probeReady, schedule = (run: () => void, ms: number) => {
  const timer = setTimeout(run, ms);
  return () => clearTimeout(timer);
}) {
  let state: Reachability = "ok";
  let origin = "";
  let cancel: (() => void) | null = null;
  const listeners = new Set<() => void>();
  const recovered = new Set<() => void>();

  const set = (next: Reachability) => {
    if (next === state) return;
    state = next;
    listeners.forEach(listener => listener());
  };

  async function check() {
    cancel = null;
    const ready = await probe(origin);
    if (ready) {
      const wasUnreachable = state === "unreachable";
      set("ok");
      if (wasUnreachable) recovered.forEach(listener => listener());
      return;
    }
    set("unreachable");
    if (!cancel) cancel = schedule(() => void check(), READY_POLL_MS);
  }

  return {
    get: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    /** Called after reachability returns from unreachable, to re-read what failed. */
    onRecovered(listener: () => void) {
      recovered.add(listener);
      return () => { recovered.delete(listener); };
    },
    /** A response said the database is unavailable: confirm with `/api/ready`. */
    note(responseOrigin: string) {
      origin = responseOrigin;
      if (state !== "ok") return; // Already checking, or polling until it passes.
      set("checking");
      void check();
    },
    reset() {
      cancel?.();
      cancel = null;
      set("ok");
    },
  };
}

export const reachability = createReachability();

/** For the transport: note a response that names the database as unavailable. */
export function noteDatabaseResponse(response: Response, origin: string): void {
  if (response.status === 503 && response.headers.get("X-Anteroom-Reason") === DATABASE_UNAVAILABLE_REASON) {
    reachability.note(origin);
  }
}
