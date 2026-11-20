import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { reportError } from "./errorReports";
import { ApiRequestError } from "./transport";
import { LIVE_IDLE_POLL_MS } from "./polling";

/** How long a read counts as fresh. Studio talks to its local server, so rereading is cheap. */
export const STALE_MS = 5000;
/**
 * While the hosted change feed is subscribed, every change re-reads the screens
 * it covers, so a read stays fresh as long as the feed's backup pass. Opening
 * another listing then reuses the account-wide reads instead of asking again.
 */
export const LIVE_STALE_MS = LIVE_IDLE_POLL_MS;

/**
 * How many times a read the server couldn't answer yet (a 502/503/504 while it
 * restarts, or a dropped connection) is asked again: about 45-90 seconds of
 * waiting, so a deploy passes unnoticed and a real outage still shows its error.
 * Other failures are retried once, as before. Writes are mutations, which
 * never retry.
 */
export const UNAVAILABLE_RETRIES = 13;
const LONGEST_RETRY_MS = 8000;

export function retryRead(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiRequestError && error.retryable) return failureCount < UNAVAILABLE_RETRIES;
  return failureCount < 1;
}

/** Waits double up to 8 seconds, with jitter so tabs don't all come back at once; the server's `Retry-After` wins. */
export function readRetryDelay(failureCount: number, error: unknown, random = Math.random): number {
  if (!(error instanceof ApiRequestError && error.retryable)) return Math.min(1000 * 2 ** failureCount, 30000);
  if (error.retryAfterMs !== null) return Math.min(error.retryAfterMs, 30000);
  const ceiling = Math.min(LONGEST_RETRY_MS, 1000 * 2 ** failureCount);
  return ceiling / 2 + random() * (ceiling / 2);
}

export function createStudioQueryClient() {
  return new QueryClient({
    queryCache: new QueryCache({ onError: error => reportError("query", error) }),
    mutationCache: new MutationCache({ onError: error => reportError("mutation", error) }),
    defaultOptions: {
      // Studio talks to its local server. The webview's Internet connection
      // status must not pause local reads or a confirmed listing action.
      queries: {
        networkMode: "always",
        staleTime: STALE_MS,
        retry: retryRead,
        retryDelay: readRetryDelay,
      },
      mutations: {
        networkMode: "always",
      },
    },
  });
}

export function setLiveFreshness(client: QueryClient, live: boolean): void {
  const defaults = client.getDefaultOptions();
  client.setDefaultOptions({ ...defaults, queries: { ...defaults.queries, staleTime: live ? LIVE_STALE_MS : STALE_MS } });
}
