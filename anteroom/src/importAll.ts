import { useSyncExternalStore } from "react";
import { api } from "./api/client";
import { directAccountKey } from "./api/directConnections";
import { marketplaceName } from "./components/marketplaceNames";
import type {
  ImportMarketplace, MarketplaceImportHeld, MarketplaceImportMatch, MarketplaceImportPage, MarketplaceImportResult,
} from "./api/inventoryImport";

export interface Importer {
  candidates: (page: number, cursor: string | null) => Promise<MarketplaceImportPage>;
  importListing: (listing: string, linkConversationId?: string) => Promise<MarketplaceImportResult>;
  /** Import a listing unless it now looks like an item already in Anteroom; the server checks as it saves. */
  /** ``account``: Chrome marketplaces, the account the run's pages came from; the server refuses a listing read from another. */
  importNew: (listing: string, account?: string) => Promise<MarketplaceImportResult | MarketplaceImportHeld>;
  /** eBay and Etsy: the connected account's ``directAccountKey``, or null when none is. A run stays on the account it was started for. */
  account?: () => Promise<string | null>;
}

export function importerFor(marketplace: ImportMarketplace): Importer {
  if (marketplace === "ebay" || marketplace === "etsy") {
    const direct = api[marketplace];
    return {
      account: async () => {
        const account = (await api.directConnections.status()).accounts[marketplace];
        return account.connected ? directAccountKey(account) : null;
      },
      candidates: (page) => direct.importCandidates(page),
      importListing: (listing, linkConversationId) => linkConversationId ? direct.importListing(listing, linkConversationId) : direct.importListing(listing),
      importNew: (listing) => direct.importNew(listing),
    };
  }
  return {
    candidates: (_page, cursor) => api.chromeImport.importCandidates(marketplace, cursor),
    importListing: (listing, linkConversationId) => api.chromeImport.importListing(marketplace, listing, linkConversationId),
    importNew: (listing, account) => api.chromeImport.importNew(marketplace, listing, account),
  };
}

/** A listing that looks like an item already in Anteroom. The seller links it or imports it as new. */
export interface ImportAllReview { id: string; title: string; price: string | null; match: MarketplaceImportMatch }

export interface ImportAllRun {
  /** Tells this run from a later one on the same key, so a cleared or replaced run's walk stops. */
  run: number;
  marketplace: ImportMarketplace;
  status: "running" | "stopping" | "done" | "stopped" | "failed";
  /** The page being read or imported. */
  page: number;
  imported: number;
  alreadyIn: number;
  skipped: number;
  review: ImportAllReview[];
  failures: { id: string; title: string; message: string }[];
  /** What an imported listing still needs, such as photos the marketplace did not provide. */
  warnings: { id: string; title: string; message: string }[];
  /** Why the run ended early: a page of listings could not be read. */
  error: string | null;
}

// Runs live outside the page, so leaving the import screen does not stop one.
// Each marketplace and account runs on its own; none waits for another.
const runs = new Map<string, ImportAllRun>();
const listeners = new Set<() => void>();
let counter = 0;

function notify() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function importAllRun(key: string): ImportAllRun | undefined {
  return runs.get(key);
}

export function useImportAll(key: string) {
  return useSyncExternalStore(subscribe, () => runs.get(key), () => runs.get(key));
}

// This many listings failing in a row means the connection is gone (Chrome closed, signed out), not the listings.
const FAILURE_STREAK = 5;
const ACCOUNT_CHANGED: Partial<Record<ImportMarketplace, string>> = {
  ebay: "The connected eBay account changed, so this import stopped. Open Import from eBay to import from the account connected now.",
  etsy: "The connected Etsy shop changed, so this import stopped. Open Import from Etsy to import from the shop connected now.",
};
const busy = (run: ImportAllRun | undefined) => run?.status === "running" || run?.status === "stopping";
const message = (error: unknown) => error instanceof Error ? error.message : "This listing could not be imported.";

/**
 * Import every active listing on one marketplace that is not in Anteroom yet.
 *
 * Reads the marketplace and saves local items through the same requests as
 * importing one listing; nothing is listed, changed or delisted. A listing
 * that looks like an existing item is never linked or duplicated here: it
 * waits in ``review`` for the seller. The server checks for a match again as
 * it saves, so marketplaces importing side by side do not import one item twice.
 */
export function startImportAll(key: string, marketplace: ImportMarketplace, importer: Importer, onFinish: () => void,
  /** eBay and Etsy: the ``directAccountKey`` of the account the seller started the run for. */
  account?: string) {
  if (busy(runs.get(key))) return;
  counter += 1;
  const id = counter;
  runs.set(key, { run: id, marketplace, status: "running", page: 1, imported: 0, alreadyIn: 0, skipped: 0, review: [], failures: [], warnings: [], error: null });
  notify();
  void walk(key, id, importer, account).finally(onFinish);
}

export function stopImportAll(key: string) {
  const run = runs.get(key);
  if (run?.status === "running") update(key, run.run, { status: "stopping" });
}

/**
 * Stop and forget runs, so nothing carries over to the next seller in this tab.
 * With a marketplace, only that marketplace's runs other than ``keep``: an account that is no longer connected.
 */
export function clearImportAll(marketplace?: ImportMarketplace, keep?: string) {
  let changed = false;
  for (const [key, run] of runs) {
    if ((!marketplace || run.marketplace === marketplace) && key !== keep) {
      runs.delete(key);
      changed = true;
    }
  }
  if (changed) notify();
}

/** The seller linked or imported a listing from the review list. */
export function resolveImportAllReview(key: string, listingId: string) {
  const run = runs.get(key);
  if (run?.review.some((row) => row.id === listingId)) update(key, run.run, { review: run.review.filter((row) => row.id !== listingId) });
}

/** Change this run only while it is still the one on its key: a cleared or replaced run is left alone. */
function update(key: string, id: number, change: Partial<ImportAllRun> | ((run: ImportAllRun) => Partial<ImportAllRun>)) {
  const current = runs.get(key);
  if (current?.run !== id) return;
  runs.set(key, { ...current, ...(typeof change === "function" ? change(current) : change) });
  notify();
}

async function walk(key: string, id: number, importer: Importer, expected: string | undefined) {
  const live = () => runs.get(key)?.run === id;
  const stopping = () => live() && runs.get(key)!.status === "stopping";
  const marketplace = runs.get(key)!.marketplace;
  const direct = marketplace === "ebay" || marketplace === "etsy";
  const seen = new Set<string>();
  const cursors = new Set<string>();
  // Chrome marketplaces: the account the first page was read from. Chrome reads whichever account is signed in,
  // so a later page from another one ends the run, and each import must come from this one.
  let chromeAccount: string | undefined;
  let streak = 0;
  let page = 1;
  let cursor: string | null = null;
  for (;;) {
    if (!live()) return;
    if (stopping()) return update(key, id, { status: "stopped" });
    update(key, id, { page });
    let data: MarketplaceImportPage;
    try {
      // Pages come from whichever account is connected now, so the account is checked before and after each read:
      // a page read across a change is dropped, and the run stops. Listings of a page read before a change belong
      // to the earlier account, and the server's ownership check refuses them once another one is connected.
      // Compared with the account the seller started the run for, from the first read on.
      const changed = async () => {
        const now = await importer.account!();
        return !now || now !== expected;
      };
      if (importer.account && await changed()) return update(key, id, { status: "failed", error: ACCOUNT_CHANGED[marketplace] ?? "" });
      if (!live()) return;
      data = await importer.candidates(page, cursor);
      if (importer.account && await changed()) return update(key, id, { status: "failed", error: ACCOUNT_CHANGED[marketplace] ?? "" });
      if (!direct && data.account) {
        chromeAccount ??= data.account;
        if (data.account !== chromeAccount) {
          return update(key, id, { status: "failed", error: `Chrome is signed in to a different ${marketplaceName(marketplace)} account now, so this import stopped.` });
        }
      }
    } catch (error) {
      // Stopped while the page was loading: that is a stop, not a failure.
      return update(key, id, stopping() ? { status: "stopped" } : { status: "failed", error: message(error) });
    }
    for (const row of data.rows) {
      if (!live()) return;
      if (seen.has(row.id)) continue;
      seen.add(row.id);
      if (row.reason) update(key, id, (run) => ({ skipped: run.skipped + 1 }));
      else if (row.conversation_id) update(key, id, (run) => ({ alreadyIn: run.alreadyIn + 1 }));
      else if (row.match) {
        const match = row.match;
        update(key, id, (run) => ({ review: [...run.review, { id: row.id, title: row.title, price: row.price, match }] }));
      } else {
        if (stopping()) return update(key, id, { status: "stopped" });
        try {
          const result = await importer.importNew(row.id, chromeAccount);
          if (result.conversation_id === null) {
            // Another import brought in a matching item after this page was read.
            const { match } = result;
            update(key, id, (run) => ({ review: [...run.review, { id: row.id, title: row.title, price: row.price, match }] }));
          } else {
            const warnings = result.warnings.map((warning) => ({ id: row.id, title: row.title, message: warning }));
            update(key, id, (run) => ({ ...(result.reused ? { alreadyIn: run.alreadyIn + 1 } : { imported: run.imported + 1 }),
              ...(warnings.length ? { warnings: [...run.warnings, ...warnings] } : {}) }));
          }
          streak = 0;
        } catch (error) {
          streak += 1;
          update(key, id, (run) => ({ failures: [...run.failures, { id: row.id, title: row.title, message: message(error) }] }));
          if (streak >= FAILURE_STREAK) return update(key, id, { status: "failed", error: message(error) });
        }
      }
    }
    // eBay and Etsy page by number; Chrome marketplaces by their own cursor, which must lead somewhere new.
    const next = data.next_cursor ?? null;
    if (!data.has_more) break;
    if (!direct && (!next || cursors.has(next))) {
      return update(key, id, { status: "failed", error: `${marketplaceName(marketplace)} said there were more listings but did not give the next page.` });
    }
    if (next) cursors.add(next);
    page += 1;
    cursor = next;
  }
  update(key, id, { status: stopping() ? "stopped" : "done" });
}
