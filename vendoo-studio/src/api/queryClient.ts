import { QueryClient } from "@tanstack/react-query";
import { LIVE_IDLE_POLL_MS } from "./polling";

/** How long a read counts as fresh. Studio talks to its local server, so rereading is cheap. */
export const STALE_MS = 5000;
/**
 * While the hosted change feed is subscribed, every change re-reads the screens
 * it covers, so a read stays fresh as long as the feed's backup pass. Opening
 * another listing then reuses the account-wide reads instead of asking again.
 */
export const LIVE_STALE_MS = LIVE_IDLE_POLL_MS;

export function createStudioQueryClient() {
  return new QueryClient({
    defaultOptions: {
      // Studio talks to its local server. The webview's Internet connection
      // status must not pause local reads or a confirmed listing action.
      queries: {
        networkMode: "always",
        staleTime: STALE_MS,
        retry: 1,
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
