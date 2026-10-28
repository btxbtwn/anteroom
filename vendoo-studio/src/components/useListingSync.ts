import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { pollMs } from "../api/polling";
import { useListingSessionActive } from "./listingSession";

/**
 * Keeps an open listing in step with Vendoo. There is no button.
 *
 * Vendoo's edits come in on their own: when the seller saves in Vendoo (the
 * extension tells Studio), when the listing opens, and when Studio regains
 * focus — never on a timer against Vendoo. Status/date-only changes refresh
 * labels without rewriting listing fields; form-content changes take Vendoo's
 * copy only when Studio has none of its own. When both moved, Studio's listing
 * stays and `vendooConflict` says so.
 *
 * Direct marketplace listings are not checked here: List and Delist report
 * their own results, and sales arrive on their own.
 */
export function useListingSync(convId: string, vendooBound: boolean): { vendooConflict: boolean } {
  const queryClient = useQueryClient();
  const sessionActive = useListingSessionActive();

  // Reads SQLite only, so it can poll: a pull started by a save in Vendoo
  // lands server-side and shows up here on the next read.
  const { data: status } = useQuery({
    queryKey: ["vendoo-sync", convId],
    queryFn: () => api.vendooApi.syncStatus(convId),
    enabled: vendooBound,
    refetchInterval: () => (sessionActive ? pollMs(5000) : false),
  });

  const refreshListing = React.useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["listing", convId] });
    queryClient.invalidateQueries({ queryKey: ["listing-fields", convId] });
    queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
    queryClient.invalidateQueries({ queryKey: ["vendoo-item"] });
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
  const { mutate: runVendooSync } = useMutation({
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

  React.useEffect(() => {
    if (!vendooBound) return undefined;
    const check = () => {
      if (!inFlight.current) runVendooSync();
    };
    check();
    window.addEventListener("focus", check);
    return () => {
      window.removeEventListener("focus", check);
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [convId, vendooBound, runVendooSync]);

  return { vendooConflict: vendooBound && Boolean(status?.conflict) };
}
