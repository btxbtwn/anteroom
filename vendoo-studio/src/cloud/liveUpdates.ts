/**
 * Live updates for the hosted workspace. The API records which part of the
 * seller's data changed in `seller_changes` (topic names and times only);
 * Supabase Realtime tells every signed-in device, and each re-reads the
 * screens that topic covers. Without a live channel, polling carries on.
 */
import type { SupabaseClient } from "@supabase/supabase-js";
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { setLiveUpdates } from "../api/polling";

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

/** Re-reads every query a set of topics covers; screens not on display only go stale. */
export function invalidateTopics(queryClient: QueryClient, topics: Iterable<string>): void {
  const wanted = [...topics];
  if (!wanted.length) return;
  void queryClient.invalidateQueries({
    predicate: (query) => wanted.some((topic) => queryKeyMatchesTopic(topic, query.queryKey)),
  });
}

/** Bursts (a send writes many rows) arrive as one refresh. */
const COALESCE_MS = 300;

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
  const flush = () => {
    timer = undefined;
    const topics = pending;
    pending = new Set();
    invalidateTopics(queryClient, topics);
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
      // Changes made while the channel was down were never announced.
      if (live && wasSubscribed) invalidateTopics(queryClient, Object.keys(TOPIC_QUERY_KEYS));
      wasSubscribed ||= live;
    });
  }).catch(() => setLiveUpdates(false));
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    setLiveUpdates(false);
    void supabase.removeChannel(channel);
  };
}
