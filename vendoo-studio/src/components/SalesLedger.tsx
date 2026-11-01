import { cn } from "../lib/utils";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { DownloadLink } from "./DownloadLink";
import { FINANCIAL_FIELDS, type FinancialEntry, type FinancialField, type SalesLedgerPage, type SalesLedgerRow } from "../api/salesLedger";
import { pollMs } from "../api/polling";
import { marketplaceName } from "./marketplaceNames";
import { useVendooEnabled } from "../vendooMode";
import { EmptyRow, PageError, PageHeader, PageNote, PageSection, PageSkeleton, Panel } from "./WorkspacePage";
import { Button, buttonVariants } from "./ui/button";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";

const MARKETS = ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted", "facebook", "other"];
const LABELS: Record<FinancialField, string> = { item_revenue: "Item revenue total", shipping_credit: "Shipping collected", refunds: "Revenue refunded", fees: "Net fees", shipping_cost: "Shipping cost total", item_cost: "Item cost total" };

export function ledgerMoney(value: string | null | undefined, currency: string | undefined): string {
  return value == null || !currency ? "Not recorded" : `${currency} ${value}`;
}

export function SalesLedgerSummary({ summary }: { summary: SalesLedgerPage["summary"] }) {
  return <>
    <PageNote>{summary.lines} order lines · {summary.unrecorded} without recorded amounts · {summary.needs_review} need review · {summary.unmatched} need an inventory link. Totals cover the full filtered period; currencies stay separate.</PageNote>
    {summary.currencies.length ? <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {summary.currencies.map((group) => <Panel key={group.currency} className="space-y-2 p-3" divided={false}>
        <p className="text-sm font-medium">{group.currency}</p>
        <p className="text-xs">Net revenue: {ledgerMoney(group.net_revenue, group.currency)} · known on {group.net_known} of {group.lines} lines</p>
        <p className="text-xs">Recorded profit: {ledgerMoney(group.profit, group.currency)} · known on {group.profit_known} of {group.lines} lines</p>
      </Panel>)}
    </div> : null}
  </>;
}

export function SalesLedger({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const vendooOn = useVendooEnabled();
  const queryClient = useQueryClient();
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const [start, setStart] = useState(() => new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(today);
  const [marketplace, setMarketplace] = useState("all");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<SalesLedgerRow | null>(null);
  const [editor, setEditor] = useState(0);
  const filters = { start, end, ...(marketplace !== "all" ? { marketplace } : {}) };
  const valid = Boolean(start && end && start >= "1970-01-01" && start <= end && end <= today);
  const query = useQuery({ queryKey: ["sales-ledger", filters, page], queryFn: () => api.salesLedger.list(filters, page),
    enabled: valid, retry: false, refetchInterval: () => pollMs(15000) });
  const data = valid ? query.data : undefined;
  return <>
    <PageHeader title="Analytics" description={vendooOn
      ? "Record sales amounts, fees, refunds and costs in Anteroom. Export your inventory and sales without Vendoo."
      : "Record sales amounts, fees, refunds and costs in Anteroom. Export your inventory and sales from here."} actions={<div className="flex flex-wrap gap-2">
      <DownloadLink className={buttonVariants({ size: "sm", variant: "outline" })} href={api.salesLedger.inventoryCsv()} filename="anteroom-inventory.csv">Export inventory CSV</DownloadLink>
      {valid ? <DownloadLink className={buttonVariants({ size: "sm", variant: "outline" })} href={api.salesLedger.salesCsv(filters)} filename="anteroom-sales.csv">Export sales CSV</DownloadLink> : <Button size="sm" variant="outline" disabled>Export sales CSV</Button>}
    </div>}>
      <div className="flex flex-wrap items-end gap-3 text-xs">
        <label className="space-y-1">Sales from (UTC)<Input type="date" min="1970-01-01" max={end || today} value={start} onChange={(event) => { setStart(event.target.value); setPage(1); }} /></label>
        <label className="space-y-1">Through (UTC)<Input type="date" min={start || "1970-01-01"} max={today} value={end} onChange={(event) => { setEnd(event.target.value); setPage(1); }} /></label>
        <Select value={marketplace} onValueChange={(value) => { if (value) setMarketplace(value); setPage(1); }}>
          <SelectTrigger aria-label="Sales marketplace" className="w-40"><SelectValue>{marketplace === "all" ? "All marketplaces" : marketplaceName(marketplace)}</SelectValue></SelectTrigger>
          <SelectPopup><SelectItem value="all">All marketplaces</SelectItem>{MARKETS.map((market) => <SelectItem key={market} value={market}>{marketplaceName(market)}</SelectItem>)}</SelectPopup>
        </Select>
        <Button size="sm" variant="ghost" disabled={!valid || query.isFetching} onClick={() => void query.refetch()}>Refresh records</Button>
      </div>
    </PageHeader>
    {!valid ? <PageNote>Choose a valid UTC date range through today.</PageNote> : query.isPending ? <PageSkeleton label="Loading sales records…" /> : null}
    {query.error ? <PageError message={query.error.message} /> : null}
    {data ? <>
      <SalesLedgerSummary summary={data.summary} />
      <PageSection title="Sales records" description="One record per order line, including cancellations and refunds. Record totals after discounts; leave an amount blank if unknown and enter 0 once confirmed.">
        {data.rows.length ? <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/60 bg-card/40">{data.rows.map((row) => <li key={row.id} className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 text-sm">
          <div className="flex min-w-0 flex-1 basis-48 flex-col gap-0.5 break-words"><p className="font-medium">{row.title || `${marketplaceName(row.marketplace)} order ${row.order_id}`}</p>
            <p className="text-xs text-muted-foreground">{marketplaceName(row.marketplace)} · {row.order_status.replace(/_/g, " ")} · quantity {row.quantity ?? "unknown"}</p>
            <p className="font-mono text-2xs text-muted-foreground/80">Order {row.order_id} · line {row.line_id}</p>
            {row.financials_stale ? <p className="text-xs text-warning">Order changed or recorded amounts need review.</p> : null}
          </div>
          <p className={cn("shrink-0 text-xs tabular-nums", row.profit == null && "text-muted-foreground")}>Profit: {ledgerMoney(row.profit, row.financials?.currency)}</p>
          <div className="flex flex-wrap gap-2">{row.conversation_id ? <Button size="xs" variant="ghost" onClick={() => onOpenListing(row.conversation_id!)}>Open item</Button> : null}
            <Button size="xs" variant="outline" onClick={() => { setSelected(row); setEditor((value) => value + 1); }}>Record amounts</Button></div>
        </li>)}</ul> : <EmptyRow>No recorded orders in this period. Recover orders from a marketplace’s Orders tab to add missing sales.</EmptyRow>}
        <div className="flex items-center gap-2 text-xs"><span>Page {page}</span><Button size="xs" variant="outline" disabled={page === 1 || query.isFetching} onClick={() => setPage(page - 1)}>Previous</Button><Button size="xs" variant="outline" disabled={!data.has_more || query.isFetching} onClick={() => setPage(page + 1)}>Next</Button></div>
      </PageSection>
    </> : null}
    {selected ? <FinancialEditor key={`${selected.id}:${editor}`} row={selected} onClose={() => setSelected(null)}
      onReload={(row) => { setSelected(row); setEditor((value) => value + 1); }} onSaved={() => {
        void queryClient.invalidateQueries({ queryKey: ["sales-ledger"] });
        void queryClient.invalidateQueries({ queryKey: ["marketplace-sales"] });
        setSelected(null);
      }} /> : null}
  </>;
}

function FinancialEditor({ row, onClose, onSaved, onReload }: { row: SalesLedgerRow; onClose: () => void; onSaved: () => void; onReload: (row: SalesLedgerRow) => void }) {
  const [currency, setCurrency] = useState(row.financials?.currency || "");
  const [values, setValues] = useState<Record<FinancialField, string>>(() => Object.fromEntries(FINANCIAL_FIELDS.map((field) => [field, row.financials?.[field] || ""])) as Record<FinancialField, string>);
  const save = useMutation({ mutationFn: () => api.salesLedger.save(row.id, {
    currency, ...Object.fromEntries(FINANCIAL_FIELDS.map((field) => [field, values[field].trim() || null])) as Omit<FinancialEntry, "currency">,
    expected_revision: row.financial_revision, expected_order_basis: row.order_basis,
  }), retry: false, onSuccess: onSaved });
  const reload = useMutation({ mutationFn: () => api.salesLedger.get(row.id), retry: false, onSuccess: onReload });
  const busy = save.isPending || reload.isPending;
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogPopup className="sm:max-w-xl"><DialogHeader className="shrink-0"><DialogTitle>Record sale amounts</DialogTitle><DialogDescription>{marketplaceName(row.marketplace)} order {row.order_id} · line {row.line_id} · quantity {row.quantity ?? "unknown"} · {row.order_status.replace(/_/g, " ")}</DialogDescription></DialogHeader>
      <form className="flex min-h-0 flex-col" onSubmit={(event) => { event.preventDefault(); if (!busy) save.mutate(); }}><DialogPanel className="space-y-4">
        <PageNote>Enter totals for this order line, excluding buyer taxes. Blank is unknown; zero is a confirmed zero. Net fees may be negative for fee credits. Saving records your figures locally; quantities and marketplace listings stay unchanged.</PageNote>
        {row.financials_stale ? <PageNote>This order changed. Review every amount before saving again.</PageNote> : null}
        <label className="block space-y-1 text-xs">Currency code<Input required pattern="[A-Z]{3}" maxLength={3} value={currency} placeholder="USD" disabled={busy} onChange={(event) => setCurrency(event.target.value.toUpperCase())} /></label>
        <div className="grid gap-3 sm:grid-cols-2">{FINANCIAL_FIELDS.map((field) => <label key={field} className="space-y-1 text-xs">{LABELS[field]}<Input inputMode="decimal" value={values[field]} placeholder="Unknown" disabled={busy} onChange={(event) => setValues((previous) => ({ ...previous, [field]: event.target.value }))} /></label>)}</div>
        {save.error ? <PageError message={save.error.message} /> : null}{reload.error ? <PageError message={reload.error.message} /> : null}
      </DialogPanel><DialogFooter className="shrink-0"><Button type="button" variant="ghost" disabled={busy} onClick={() => reload.mutate()}>Reload record</Button><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Cancel</Button><Button type="submit" disabled={busy || !/^[A-Z]{3}$/.test(currency)}>{save.isPending ? "Saving…" : "Save amounts"}</Button></DialogFooter></form>
    </DialogPopup>
  </Dialog>;
}
