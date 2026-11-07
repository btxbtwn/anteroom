/**
 * Live updates for the hosted workspace. The API records which part of the
 * seller's data changed in `seller_changes` (topic names and times only);
 * Supabase Realtime tells every signed-in device, and each re-reads the
 * screens that topic covers, so reads stay fresh longer while it is subscribed.
 * Without a live channel, polling carries on.
 */
import type { SupabaseClient } from "@supabase/supabase-js";
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { setLiveUpdates } from "../api/polling";
import { setLiveFreshness } from "../api/queryClient";

/**
 * The topics the server writes (`TOPIC_BY_TABLE` in
 * server/vendoo_studio/models/seller_change.py) and the query keys each
 * refreshes. A key ending in "*" matches every key that starts with it.
 */
export const TOPIC_QUERY_KEYS: Record<string, readonly string[]> = {
  listings: [
    "status", "conversations", "conversation", "listing", "listing-fields", "messages", "revisions", "revision",
    "published-versions", "marketplace-listings", "marketplace-statuses", "native-marketplaces", "native-editor-form",
    "native-preflight", "listing-videos", "similar-items", "suggestions", "activity", "analytics", "sales-ledger",
  ],
  photos: ["photos", "conversations", "conversation", "listing", "marketplace-media"],
  jobs: ["status", "jobs", "queue", "fill-log", "activity", "conversations", "marketplace-statuses"],
  settings: [
    "settings-*", "ai-keys", "ai-key-models", "direct-connections", "marketplace-sessions", "listing-templates",
    "ebay-listing-defaults", "ebay-listing-profiles", "etsy-listing-defaults", "etsy-listing-profiles", "status",
  ],
  assistant: ["assistant"],
  sales: ["marketplace-sales", "sales-ledger", "sale-calendar", "sale-detection", "analytics", "conversations"],
  automations: [
    "automations", "automation-approvals", "automation-gaps", "promotion-automations", "promotions",
    "promotion-floors", "ebay-offers", "price-drop-preview",
  ],
  sourcing: ["sourcing", "boxes", "scout"],
};

export function queryKeyMatchesTopic(topic: string, queryKey: QueryKey): boolean {
  const head = queryKey[0];
  if (typeof head !== "string") return false;
  return (TOPIC_QUERY_KEYS[topic] ?? []).some((key) =>
    key.endsWith("*") ? head.startsWith(key.slice(0, -1)) : head === key);
}

/**
 * Re-reads every query a set of topics covers; screens not on display only go
 * stale. `except` leaves one query key out, for a caller that refreshes it on
 * its own schedule.
 */
export async function invalidateTopics(queryClient: QueryClient, topics: Iterable<string>, except?: string): Promise<void> {
  const wanted = [...topics];
  if (!wanted.length) return;
  await refreshMatching(queryClient, (queryKey) => queryKey[0] !== except
    && wanted.some((topic) => queryKeyMatchesTopic(topic, queryKey)));
}

async function refreshMatching(queryClient: QueryClient, matches: (queryKey: QueryKey) => boolean): Promise<void> {
  const filters = { predicate: (query: { queryKey: QueryKey }) => matches(query.queryKey) };
  // Cancelling a browser query cannot stop synchronous work already running
  // on the API. A change burst must not start a fresh inventory scan for each
  // event while previous scans still occupy database connections. Wait for
  // current reads, then fetch the change; overlapping refreshes share that
  // next read. Merely sharing the current read could miss the newer change.
  const running = queryClient.getQueryCache().findAll(filters)
    .filter(query => query.state.fetchStatus === "fetching")
    .flatMap(query => query.promise ? [query.promise] : []);
  if (running.length) await Promise.allSettled(running);
  await queryClient.invalidateQueries(filters, { cancelRefetch: false });
}

/** Bursts (a send writes many rows) arrive as one refresh. */
const COALESCE_MS = 300;
/**
 * The inventory list is the heaviest read and almost every topic covers it.
 * While a job writes changes every few hundred milliseconds, the list is
 * re-read at most this often; the last change in a burst is always fetched.
 */
export const INVENTORY_REFRESH_MS = 5000;
const INVENTORY_KEY = "conversations";
const coversInventory = (topic: string) => (TOPIC_QUERY_KEYS[topic] ?? []).includes(INVENTORY_KEY);

type ChannelStatus = "SUBSCRIBED" | "TIMED_OUT" | "CLOSED" | "CHANNEL_ERROR";

/**
 * Follows the signed-in seller's change feed. Returns the cleanup that
 * unsubscribes. Polling relaxes only while the channel is subscribed.
 */
export function followSellerChanges(
  supabase: Pick<SupabaseClient, "channel" | "removeChannel" | "realtime">,
  userId: string,
  accessToken: string,
  queryClient: QueryClient,
): () => void {
  let stopped = false;
  let pending = new Set<string>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let wasSubscribed = false;
  let inventoryReadAt = -Infinity;
  let inventoryTimer: ReturnType<typeof setTimeout> | undefined;
  const refreshInventory = () => {
    inventoryTimer = undefined;
    inventoryReadAt = Date.now();
    void refreshMatching(queryClient, (queryKey) => queryKey[0] === INVENTORY_KEY).catch(() => {});
  };
  const flush = () => {
    timer = undefined;
    const topics = pending;
    pending = new Set();
    const inventory = [...topics].some(coversInventory);
    const wait = inventoryReadAt + INVENTORY_REFRESH_MS - Date.now();
    if (!inventory || wait <= 0) {
      if (inventory) inventoryReadAt = Date.now();
      void invalidateTopics(queryClient, topics).catch(() => {});
      return;
    }
    void invalidateTopics(queryClient, topics, INVENTORY_KEY).catch(() => {});
    inventoryTimer ??= setTimeout(refreshInventory, wait);
  };
  const channel = supabase.channel(`seller-changes:${userId}`).on(
    "postgres_changes",
    { event: "*", schema: "public", table: "seller_changes", filter: `user_id=eq.${userId}` },
    (payload: { new?: { topic?: unknown } }) => {
      const topic = payload.new?.topic;
      if (stopped || typeof topic !== "string") return;
      pending.add(topic);
      timer ??= setTimeout(flush, COALESCE_MS);
    },
  );
  // supabase-js keeps the Realtime token current on refresh after this.
  void supabase.realtime.setAuth(accessToken).then(() => {
    if (stopped) return;
    channel.subscribe((status: ChannelStatus) => {
      if (stopped) return;
      const live = status === "SUBSCRIBED";
      setLiveUpdates(live);
      setLiveFreshness(queryClient, live);
      // Changes made while the channel was down were never announced.
      if (live && wasSubscribed) invalidateTopics(queryClient, Object.keys(TOPIC_QUERY_KEYS));
      wasSubscribed ||= live;
    });
  }).catch(() => {
    setLiveUpdates(false);
    setLiveFreshness(queryClient, false);
  });
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    if (inventoryTimer) clearTimeout(inventoryTimer);
    setLiveUpdates(false);
    setLiveFreshness(queryClient, false);
    void supabase.removeChannel(channel);
  };
}
