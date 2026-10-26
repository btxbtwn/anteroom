import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DirectMarketplace } from "../api/directConnections";
import type { OrderRecoveryRequest, OrderRecoveryResult } from "../api/orderRecovery";
import { marketplaceName } from "./marketplaceNames";
import { PageError, PageNote, PageSection } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

type Totals = Pick<OrderRecoveryResult, "ordersScanned" | "skippedOrders" | "recorded" | "updated" | "unchanged" | "conflicts" | "matched">;
const EMPTY: Totals = { ordersScanned: 0, skippedOrders: 0, recorded: 0, updated: 0, unchanged: 0, conflicts: 0, matched: 0 };

export function OrderRecovery({ marketplace, connectedAt }: { marketplace: DirectMarketplace; connectedAt: number | null }) {
  const queryClient = useQueryClient();
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const [earliest] = useState(() => new Date(Date.now() - 89 * 86400000).toISOString().slice(0, 10));
  const [start, setStart] = useState(() => new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(today);
  const [totals, setTotals] = useState<Totals | null>(null);
  const [nextPage, setNextPage] = useState<number | null>(null);
  const [stopping, setStopping] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const resume = useRef<OrderRecoveryRequest | null>(null);
  const stop = useRef(false);
  useEffect(() => () => { stop.current = true; }, []);
  const valid = Boolean(start && end && start >= earliest && end <= today && start <= end &&
    (Date.parse(end) - Date.parse(start)) / 86400000 <= 29);
  const run = useMutation({
    mutationFn: async (restart: boolean) => {
      stop.current = false;
      setStopping(false);
      let page: OrderRecoveryRequest | null = restart
        ? { marketplace, start, end, page: 1, connected_at: String(connectedAt) }
        : resume.current;
      if (restart) { setTotals({ ...EMPTY }); setTruncated(false); }
      while (page && !stop.current) {
        resume.current = page;
        setNextPage(page.page);
        const result = await api.marketplaceSales.recover(page);
        setTotals((previous) => {
          const total = { ...(previous || EMPTY) };
          for (const field of Object.keys(EMPTY) as (keyof Totals)[]) total[field] += result[field];
          return total;
        });
        setTruncated(result.truncated);
        page = result.nextPage == null ? null : { ...page, page: result.nextPage, connection: result.connection, through: result.through };
        resume.current = page;
        setNextPage(page?.page ?? null);
      }
    },
    retry: false,
    onSettled: () => {
      setStopping(false);
      for (const key of ["marketplace-sales", "marketplace-listings", "marketplace-statuses", "conversations", "ebay-offers"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
  function reset() { resume.current = null; setNextPage(null); setTotals(null); setTruncated(false); run.reset(); }
  return <PageSection title={`Recover ${marketplaceName(marketplace)} orders`} description="Check orders changed during these dates (UTC), including updates to older orders. Save missing sales and cancellation/refund status in Anteroom without changing marketplace listings or local quantities.">
    <PageNote>Orders are checked one page at a time. Existing sales and dismissed alerts are reused. Recovery never sends notifications, relists, or delists items. Choose up to 30 days within the last 90 days.</PageNote>
    <form className="ebay-search-form" onSubmit={(event) => { event.preventDefault(); if (valid && connectedAt != null && !run.isPending) run.mutate(true); }}>
      <label className="space-y-1 text-xs">Order changes from (UTC)<Input type="date" required min={earliest} max={end || today} value={start} disabled={run.isPending} onChange={(event) => { setStart(event.target.value); reset(); }} /></label>
      <label className="space-y-1 text-xs">Through (UTC)<Input type="date" required min={start || earliest} max={today} value={end} disabled={run.isPending} onChange={(event) => { setEnd(event.target.value); reset(); }} /></label>
      <Button type="submit" size="sm" variant="outline" disabled={!valid || connectedAt == null || run.isPending}>Recover orders</Button>
      {!run.isPending && nextPage != null ? <Button type="button" size="sm" variant="outline" onClick={() => run.mutate(false)}>Continue from page {nextPage}</Button> : null}
      {run.isPending ? <Button type="button" size="sm" variant="ghost" disabled={stopping} onClick={() => { stop.current = true; setStopping(true); }}>{stopping ? "Stopping after this page…" : "Stop after this page"}</Button> : null}
    </form>
    {connectedAt == null ? <PageNote>Reconnect this marketplace before recovering orders.</PageNote> : null}
    {!valid ? <PageNote>Choose a valid UTC date range of up to 30 days within the last 90 days.</PageNote> : null}
    {run.isPending ? <p role="status" className="text-xs">Checking order page {nextPage || 1}…</p> : null}
    {totals ? <div role="status" className="space-y-1 text-xs">
      <p>{totals.ordersScanned} orders checked · {totals.recorded} new order lines · {totals.updated} updated · {totals.unchanged} unchanged.</p>
      <p>{totals.skippedOrders} unpaid or unsupported orders skipped · {totals.matched} previously unmatched lines linked.</p>
      {totals.conflicts ? <p>{totals.conflicts} order-line identity conflicts were kept for review; existing links were preserved.</p> : null}
      {!run.isPending && nextPage != null ? <p>Stopped before page {nextPage}. Continue to check the remaining pages.</p> : null}
      {truncated ? <p>More orders remain beyond this recovery limit. Choose a narrower date range to continue.</p> : null}
    </div> : null}
    {run.error ? <PageError message={run.error.message} /> : null}
  </PageSection>;
}
