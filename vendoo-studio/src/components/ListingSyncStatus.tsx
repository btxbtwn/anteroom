import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { MarketplaceListings } from "../api/marketplaceListings";
import type { NativeDrafts, NativeSync } from "../api/nativeMarketplaces";
import { pollMs } from "../api/polling";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const UNSETTLED = new Set(["saving", "publishing", "updating", "unknown"]);
const MARKETPLACE_NAMES: Record<string, string> = {
  ebay: "eBay", etsy: "Etsy", poshmark: "Poshmark", mercari: "Mercari", depop: "Depop", sellwild: "Sellwild",
};

/** "just now", "4m ago", "2h ago", then a date. */
export function checkedAgo(checkedAt: string, now: number): string {
  const at = Date.parse(checkedAt);
  if (Number.isNaN(at)) return "";
  const elapsed = Math.max(0, now - at);
  if (elapsed < MINUTE) return "just now";
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)}m ago`;
  if (elapsed < 24 * HOUR) return `${Math.floor(elapsed / HOUR)}h ago`;
  return new Date(at).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** A listing Anteroom put up directly or the seller linked, or a direct action still to confirm. */
export function hasDirectListings(drafts: NativeDrafts | undefined, listings: MarketplaceListings | undefined): boolean {
  const live = Object.values(listings ?? {}).some(
    (entry) => entry?.source && entry.source !== "vendoo" && entry.state === "live" && !entry.sold_here,
  );
  return live || Object.values(drafts ?? {}).some((draft) => Boolean(draft?.job_id && UNSETTLED.has(draft.state)));
}

/** One line per marketplace Sync could not check. */
export function directSyncErrors(result: NativeSync | undefined): string[] {
  return Object.entries(result?.marketplaces ?? {})
    .filter(([, outcome]) => outcome && !outcome.ok)
    .map(([name, outcome]) => `${MARKETPLACE_NAMES[name] ?? name}: ${outcome?.error ?? "could not check"}`);
}

/**
 * Keeps a listing in step with Vendoo and with its direct marketplace listings.
 *
 * Vendoo's edits come in on their own: when the seller saves in Vendoo (the
 * extension tells Studio), when the listing opens, and when Studio regains
 * focus — never on a timer against Vendoo. Status/date-only changes refresh
 * labels without rewriting listing fields; form-content changes take Vendoo's
 * copy only when Studio has none of its own. When both moved, Studio's listing
 * stays and this line says so. Nothing pops up; this line says when Studio last checked.
 *
 * Direct listings are checked only on click: unconfirmed results are settled
 * and the Chrome marketplaces are asked for sales now instead of on their next
 * timed check.
 */
export function ListingSyncStatus({ convId, bound }: { convId: string; bound: boolean }) {
  const queryClient = useQueryClient();
  const [now, setNow] = React.useState(() => Date.now());

  // Reads SQLite only, so it can poll: a pull started by a save in Vendoo
  // lands server-side and shows up here on the next read.
  const { data: status } = useQuery({
    queryKey: ["vendoo-sync", convId],
    queryFn: () => api.vendooApi.syncStatus(convId),
    enabled: bound,
    refetchInterval: () => pollMs(5000),
  });
  // Same keys as the Marketplaces section, so they share its cache.
  const { data: drafts } = useQuery({
    queryKey: ["native-marketplaces", convId],
    queryFn: () => api.nativeMarketplaces.status(convId),
  });
  const { data: listings } = useQuery({
    queryKey: ["marketplace-listings", convId],
    queryFn: () => api.marketplaceListings.get(convId),
  });
  const direct = hasDirectListings(drafts, listings);

  const refreshListing = React.useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["listing", convId] });
    queryClient.invalidateQueries({ queryKey: ["listing-fields", convId] });
    queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
    queryClient.invalidateQueries({ queryKey: ["vendoo-item"] });
    queryClient.invalidateQueries({ queryKey: ["vendoo-item-peek"] });
    queryClient.invalidateQueries({ queryKey: ["marketplace-statuses"] });
    queryClient.invalidateQueries({ queryKey: ["vendoo-sync", convId] });
  }, [convId, queryClient]);

  // A new synced revision means Vendoo's version was pulled in.
  const seenRevision = React.useRef<string | null | undefined>(undefined);
  React.useEffect(() => {
    if (!status) return;
    if (seenRevision.current !== undefined && seenRevision.current !== status.revision_id) {
      refreshListing();
    }
    seenRevision.current = status.revision_id;
  }, [status, refreshListing]);

  // A check can wait minutes on a busy Chrome. One at a time, dropped when the
  // listing closes: stacked checks fill the webview's six connections and
  // queue every other request (Open listing, loads) behind them.
  const inFlight = React.useRef<AbortController | null>(null);
  const sync = useMutation({
    mutationFn: async () => {
      const controller = new AbortController();
      inFlight.current = controller;
      try {
        return await api.vendooApi.sync(convId, controller.signal);
      } catch (err) {
        if (controller.signal.aborted) return null;
        throw err;
      } finally {
        if (inFlight.current === controller) inFlight.current = null;
      }
    },
    // Label (draft/active/sold) can move without a new revision, so refresh
    // the sidebar on every check.
    onSuccess: (data) => {
      if (data) refreshListing();
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["vendoo-sync", convId] }),
  });
  const { mutate: runSync } = sync;
  const syncOnce = React.useCallback(() => {
    if (!inFlight.current) runSync();
  }, [runSync]);

  const directSync = useMutation({
    mutationFn: () => api.nativeMarketplaces.sync(convId),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["native-marketplaces", convId] });
      queryClient.invalidateQueries({ queryKey: ["marketplace-listings", convId] });
      queryClient.invalidateQueries({ queryKey: ["marketplace-sales"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", convId] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  // The last direct sync belongs to the listing it ran on.
  const { reset: resetDirectSync } = directSync;
  React.useEffect(() => resetDirectSync, [convId, resetDirectSync]);

  const syncNow = () => {
    if (bound) syncOnce();
    if (direct && !directSync.isPending) directSync.mutate();
  };

  React.useEffect(() => {
    if (!bound) return undefined;
    syncOnce();
    window.addEventListener("focus", syncOnce);
    return () => {
      window.removeEventListener("focus", syncOnce);
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [convId, bound, syncOnce]);

  const shown = bound || direct;
  React.useEffect(() => {
    if (!shown) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, [shown]);

  if (!shown) return null;

  const busy = sync.isPending || directSync.isPending;
  const vendooFailed = bound && (sync.data?.action === "unavailable" || sync.isError);
  const directErrors = directSync.isError
    ? [`Direct listings: ${directSync.error.message || "could not sync"}`]
    : directSyncErrors(directSync.data);
  const failed = vendooFailed || directErrors.length > 0;
  const checkedAt = bound ? status?.checked_at : directSync.data?.checked_at;
  const conflict = bound && Boolean(status?.conflict);
  let label: string;
  if (busy) label = "Syncing…";
  else if (conflict) label = "Vendoo differs";
  else if (checkedAt) label = `Synced ${checkedAgo(checkedAt, now)}`;
  else label = failed ? "Not synced" : bound ? "Syncing…" : "Sync";

  const title = [
    bound
      ? (status?.checked_at ? `Last checked Vendoo ${new Date(status.checked_at).toLocaleString()}` : "Not checked with Vendoo yet")
      : "",
    vendooFailed ? "Last check could not reach Vendoo — is Chrome connected?" : "",
    conflict
      ? "Vendoo's draft and this listing were both edited. This listing was kept. Update Vendoo writes it to Vendoo; Discard takes Vendoo's copy."
      : "",
    direct && directSync.data ? `Last checked direct listings ${new Date(directSync.data.checked_at).toLocaleString()}` : "",
    ...directErrors,
    !direct ? "Click to check now" : bound ? "Click to check Vendoo and your direct listings now" : "Click to check your direct listings now",
  ].filter(Boolean).join("\n");

  return (
    <span className="pr-vendoo-sync">
      <span aria-hidden="true">·</span>
      <button
        type="button"
        className={`pr-vendoo-sync-btn${(failed || conflict) && !busy ? " is-stale" : ""}`}
        disabled={busy}
        title={title}
        onMouseDown={(event) => event.stopPropagation()}
        onClick={syncNow}
      >
        {label}
      </button>
    </span>
  );
}
