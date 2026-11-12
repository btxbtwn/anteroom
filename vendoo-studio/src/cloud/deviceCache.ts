import { clear, createStore, del, get, set, type UseStore } from "idb-keyval";
import type { Query, QueryClient } from "@tanstack/react-query";
import type { PersistedClient, Persister } from "@tanstack/react-query-persist-client";

/**
 * What the hosted app keeps in this browser so a reload opens on the seller's
 * workspace instead of a startup screen: the workspace's features and the
 * account-wide reads below. Screens show the saved copy at once and refresh it
 * from the server as usual. Photos have their own device cache; chats, jobs and
 * listing details are always read fresh.
 *
 * Only one seller's data is kept. Another seller signing in, signing out or
 * deleting the account erases it, and a new build or a day's age discards it.
 */

/** First query-key part of each read kept on the device. */
export const KEPT_ON_DEVICE = [
  "conversations", "settings-ui",
  "sourcing", "boxes",
  "automations", "automation-approvals", "automation-gaps", "ebay-offers", "promotion-automations",
  "direct-connections", "sales-ledger", "analytics",
] as const;

export const DEVICE_MAX_AGE_MS = 24 * 60 * 60_000;
/** A different build may read differently shaped data, so it starts afresh. */
export const DEVICE_BUSTER = `${__STUDIO_BUILD__.version}-${__STUDIO_BUILD__.short_sha}`;
/** Opening the workspace never waits longer than this on browser storage. */
const STORAGE_DEADLINE_MS = 1000;

const OWNER = "owner";
const QUERIES = "queries";
const RUNTIME = "runtime";

let store: UseStore | null | undefined;
/** Set once the device is erased; the page reloads right after, so nothing is kept again. */
let forgotten = false;
function deviceStore(): UseStore | null {
  if (forgotten) return null;
  if (store === undefined) {
    try {
      store = typeof indexedDB === "undefined" ? null : createStore("anteroom-device", "workspace");
    } catch {
      // Private browsing or blocked site data: nothing is kept.
      store = null;
    }
  }
  return store;
}

function withDeadline<T>(work: Promise<T>, fallback: T): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<T>((resolve) => { timer = setTimeout(() => resolve(fallback), STORAGE_DEADLINE_MS); });
  return Promise.race([work.catch(() => fallback), late]).finally(() => clearTimeout(timer));
}

const claims = new Map<string, Promise<UseStore | null>>();
/** The store, once it holds nothing but this seller's data. */
function sellerStore(seller: string): Promise<UseStore | null> {
  let claim = claims.get(seller);
  if (!claim) {
    const target = deviceStore();
    claim = target ? (async () => {
      if (await get(OWNER, target) !== seller) {
        await clear(target);
        await set(OWNER, seller, target);
      }
      return target;
    })().catch(() => null) : Promise.resolve(null);
    claims.set(seller, claim);
  }
  return claim;
}

export function keepsOnDevice(query: Query): boolean {
  const first = query.queryKey[0];
  return query.state.status === "success" && typeof first === "string" && (KEPT_ON_DEVICE as readonly string[]).includes(first);
}

/**
 * Kept reads stay in memory as long as their saved copy may be used; with the
 * default five minutes an unwatched read would drop out of the next save.
 */
export function keepReadsForDevice(client: QueryClient): void {
  for (const first of KEPT_ON_DEVICE) client.setQueryDefaults([first], { gcTime: DEVICE_MAX_AGE_MS });
}

/**
 * React Query offers the whole client for saving on every cache change, and
 * writing it copies every kept read (the full inventory) on the main thread.
 * The latest state is written at most this often, and at once when the page
 * is hidden or closed.
 */
export const DEVICE_SAVE_EVERY_MS = 1000;

export function devicePersister(seller: string): Persister {
  let latest: PersistedClient | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const write = async () => {
    if (timer) clearTimeout(timer);
    timer = undefined;
    const client = latest;
    latest = null;
    if (!client) return;
    const target = await sellerStore(seller);
    if (target && !forgotten) await set(QUERIES, client, target).catch(() => {});
  };
  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", () => { void write(); });
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") void write(); });
  }
  return {
    persistClient: (client: PersistedClient) => {
      latest = client;
      timer ??= setTimeout(() => { void write(); }, DEVICE_SAVE_EVERY_MS);
    },
    restoreClient: () => withDeadline((async () => {
      const target = await sellerStore(seller);
      return target ? await get<PersistedClient>(QUERIES, target) : undefined;
    })(), undefined),
    removeClient: async () => {
      latest = null;
      const target = await sellerStore(seller);
      if (target) await del(QUERIES, target).catch(() => {});
    },
  };
}

type Saved<T> = { value: T; savedAt: number; buster: string };

/** The workspace's features from the last visit, if this build saved them within a day. */
export function savedRuntime<T>(seller: string, now = Date.now()): Promise<T | null> {
  return withDeadline((async () => {
    const target = await sellerStore(seller);
    const saved = target ? await get<Saved<T>>(RUNTIME, target) : undefined;
    if (!saved || saved.buster !== DEVICE_BUSTER || now - saved.savedAt > DEVICE_MAX_AGE_MS) return null;
    return saved.value;
  })(), null);
}

export async function saveRuntime<T>(seller: string, value: T, now = Date.now()): Promise<void> {
  const target = await sellerStore(seller);
  if (target && !forgotten) await set(RUNTIME, { value, savedAt: now, buster: DEVICE_BUSTER } satisfies Saved<T>, target).catch(() => {});
}

/** Erase everything kept for any seller on this device. */
export async function forgetDeviceWorkspace(): Promise<void> {
  const target = deviceStore();
  forgotten = true;
  claims.clear();
  if (target) await clear(target).catch(() => {});
}
