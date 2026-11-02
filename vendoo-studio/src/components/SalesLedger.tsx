import { cn } from "../lib/utils";
import { useDeferredValue, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { DownloadLink } from "./DownloadLink";
import { FINANCIAL_FIELDS, type FinancialEntry, type FinancialField, type SalesLedgerPage, type SalesLedgerRow } from "../api/salesLedger";
import { pollMs } from "../api/polling";
import { marketplaceName } from "./marketplaceNames";
import { EmptyRow, PageError, PageHeader, PageNote, PageSection, PageSkeleton, Panel } from "./WorkspacePage";
import { Button, buttonVariants } from "./ui/button";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";

const MARKETS = ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted", "other"];
const LABELS: Record<FinancialField, string> = { item_revenue: "Item revenue total", shipping_credit: "Shipping collected", refunds: "Revenue refunded", fees: "Net fees", shipping_cost: "Shipping cost total", item_cost: "Item cost total" };

export function ledgerMoney(value: string | null | undefined, currency: string | undefined): string {
  return value == null || !currency ? "Not recorded" : `${currency} ${value}`;
}

export function SalesLedgerSummary({ summary }: { summary: SalesLedgerPage["summary"] }) {
  return <>
    <PageNote>{summary.lines} sale records · {summary.unrecorded} without recorded amounts · {summary.needs_review} need review · {summary.unmatched} need an inventory link.</PageNote>
    {summary.currencies.length ? <div className="grid gap-3">
      {summary.currencies.map((group) => <Panel key={group.currency} className="space-y-2 p-3" divided={false}>
        <p className="text-sm font-medium">{group.currency}</p>
        <dl className="grid grid-cols-2 gap-4 tabular-nums">
          <div><dt className="text-xs text-muted-foreground">Net revenue</dt><dd className={cn("mt-1", group.net_revenue == null ? "text-sm text-muted-foreground" : "text-xl font-semibold")}>{ledgerMoney(group.net_revenue, group.currency)}</dd><dd className="mt-1 text-xs text-muted-foreground">known on {group.net_known} of {group.lines} lines</dd></div>
          <div><dt className="text-xs text-muted-foreground">Recorded profit</dt><dd className={cn("mt-1", group.profit == null ? "text-sm text-muted-foreground" : "text-xl font-semibold")}>{ledgerMoney(group.profit, group.currency)}</dd><dd className="mt-1 text-xs text-muted-foreground">known on {group.profit_known} of {group.lines} lines</dd></div>
        </dl>
      </Panel>)}
    </div> : null}
    {summary.currencies.length ? <PageNote>Totals reflect all matching records, with currencies kept separate. Profit includes only records with complete financial amounts.</PageNote> : null}
  </>;
}

export function SalesLedger({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const queryClient = useQueryClient();
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const [start, setStart] = useState(() => new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(today);
  const [marketplace, setMarketplace] = useState("all");
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const [attention, setAttention] = useState("all");
  const [sort, setSort] = useState("newest");
  const [period, setPeriod] = useState("30");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<SalesLedgerRow | null>(null);
  const [editor, setEditor] = useState(0);
  const filters = { start, end, q, attention, sort, ...(marketplace !== "all" ? { marketplace } : {}) };
  const valid = Boolean(start && end && start >= "1970-01-01" && start <= end && end <= today);
  const query = useQuery({ queryKey: ["sales-ledger", filters, page], queryFn: () => api.salesLedger.list(filters, page),
    enabled: valid, retry: false, refetchInterval: () => pollMs(15000) });
  const data = valid ? query.data : undefined;
  return <>
    <PageHeader title="Analytics" description="Record sales amounts, fees, refunds and costs in Anteroom. Export your inventory and sales from here." actions={<div className="flex flex-wrap gap-2">
      <DownloadLink className={buttonVariants({ size: "sm", variant: "outline" })} href={api.salesLedger.inventoryCsv()} filename="anteroom-inventory.csv">Export inventory CSV</DownloadLink>
      {valid ? <DownloadLink className={buttonVariants({ size: "sm", variant: "outline" })} href={api.salesLedger.salesCsv(filters)} filename="anteroom-sales.csv">Export sales CSV</DownloadLink> : <Button size="sm" variant="outline" disabled>Export sales CSV</Button>}
    </div>}>
      <div className="grid grid-cols-2 items-end gap-3 text-xs lg:grid-cols-4">
        <label className="col-span-2 block space-y-1">Search sales<Input type="search" placeholder="Item title or order number" value={search} onChange={event => { setSearch(event.target.value); setPage(1); }} /></label>
        <label className="block space-y-1">Period<Select value={period} onValueChange={value => {
          if (!value) return;
          setPeriod(value); setPage(1);
          if (value !== "custom") { setEnd(today); setStart(value === "all" ? "1970-01-01" : new Date(Date.parse(today) - (Number(value) - 1) * 86400000).toISOString().slice(0, 10)); }
        }}><SelectTrigger aria-label="Sales period" className="w-full"><SelectValue>{({ "30": "Last 30 days", "90": "Last 90 days", "365": "Last year", all: "All time", custom: "Custom dates" } as Record<string, string>)[period]}</SelectValue></SelectTrigger><SelectPopup>
          <SelectItem value="30">Last 30 days</SelectItem><SelectItem value="90">Last 90 days</SelectItem><SelectItem value="365">Last year</SelectItem><SelectItem value="all">All time</SelectItem><SelectItem value="custom">Custom dates</SelectItem>
        </SelectPopup></Select></label>
        {period === "custom" ? <><label className="block space-y-1">Sales from (UTC)<Input type="date" min="1970-01-01" max={end || today} value={start} onChange={(event) => { setStart(event.target.value); setPage(1); }} /></label>
        <label className="block space-y-1">Through (UTC)<Input type="date" min={start || "1970-01-01"} max={today} value={end} onChange={(event) => { setEnd(event.target.value); setPage(1); }} /></label></> : null}
        <label className="block space-y-1">Marketplace
        <Select value={marketplace} onValueChange={(value) => { if (value) setMarketplace(value); setPage(1); }}>
          <SelectTrigger aria-label="Sales marketplace" className="w-full"><SelectValue>{marketplace === "all" ? "All marketplaces" : marketplaceName(marketplace)}</SelectValue></SelectTrigger>
          <SelectPopup><SelectItem value="all">All marketplaces</SelectItem>{MARKETS.map((market) => <SelectItem key={market} value={market}>{marketplaceName(market)}</SelectItem>)}</SelectPopup>
        </Select>
        </label>
        <label className="block space-y-1">Show<Select value={attention} onValueChange={value => { if (value) setAttention(value); setPage(1); }}>
          <SelectTrigger aria-label="Sales completeness" className="w-full"><SelectValue>{({ all: "All records", missing: "Missing sale data", review: "Needs review", loss: "Losses" } as Record<string, string>)[attention]}</SelectValue></SelectTrigger><SelectPopup>
            <SelectItem value="all">All records</SelectItem><SelectItem value="missing">Missing sale data</SelectItem><SelectItem value="review">Needs review</SelectItem><SelectItem value="loss">Losses</SelectItem>
          </SelectPopup></Select></label>
        <label className="block space-y-1">Sort<Select value={sort} onValueChange={value => { if (value) setSort(value); setPage(1); }}>
          <SelectTrigger aria-label="Sort sales" className="w-full"><SelectValue>{({ newest: "Newest first", oldest: "Oldest first", title: "Item title" } as Record<string, string>)[sort]}</SelectValue></SelectTrigger><SelectPopup><SelectItem value="newest">Newest first</SelectItem><SelectItem value="oldest">Oldest first</SelectItem><SelectItem value="title">Item title</SelectItem></SelectPopup>
        </Select></label>
        {search || attention !== "all" || marketplace !== "all" || sort !== "newest" || period !== "30" ? <Button size="sm" variant="ghost" onClick={() => { setSearch(""); setAttention("all"); setMarketplace("all"); setSort("newest"); setPeriod("30"); setEnd(today); setStart(new Date(Date.parse(today) - 29 * 86400000).toISOString().slice(0, 10)); setPage(1); }}>Clear filters</Button> : null}
        <Button size="sm" variant="ghost" disabled={!valid || query.isFetching} onClick={() => void query.refetch()}>Refresh records</Button>
      </div>
    </PageHeader>
    {!valid ? <PageNote>Choose a valid UTC date range through today.</PageNote> : query.isPending ? <PageSkeleton label="Loading sales records…" /> : null}
    {query.error ? <PageError message={query.error.message} /> : null}
    {data ? <>
      <SalesLedgerSummary summary={data.summary} />
      <PageSection title="Sales records" description="Imported sales and marketplace order lines, including cancellations and refunds. Record totals after discounts; leave an amount blank if unknown and enter 0 once confirmed.">
        {data.rows.length ? <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/60 bg-card/40">{data.rows.map((row) => <li key={row.id} className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 text-sm">
          <div className="flex min-w-0 flex-1 basis-48 flex-col gap-0.5 break-words"><p className="font-medium">{row.title || `${marketplaceName(row.marketplace)} order ${row.order_id}`}</p>
            <p className="text-xs text-muted-foreground">{row.sold_at ? row.sold_at.slice(0, 10) : "Sale date missing"} · {marketplaceName(row.marketplace)} · {row.order_status.replace(/_/g, " ")} · quantity {row.quantity ?? "unknown"}</p>
            <details className="text-2xs text-muted-foreground"><summary className="cursor-pointer">Order details</summary><p className="font-mono">Order {row.order_id} · line {row.line_id}</p></details>
            {row.missing_sale_data?.length ? <p className="text-xs text-warning-foreground">Missing: {row.missing_sale_data.join(", ")}</p> : null}
            {row.financials_stale ? <p className="text-xs text-warning-foreground">Order changed or recorded amounts need review.</p> : null}
          </div>
          <dl className="grid w-full grid-cols-3 gap-4 text-right text-xs tabular-nums sm:w-72">
            <div><dt className="text-muted-foreground">Item revenue</dt><dd className="mt-1">{ledgerMoney(row.financials?.item_revenue, row.financials?.currency)}</dd></div>
            <div><dt className="text-muted-foreground">Fees</dt><dd className="mt-1">{ledgerMoney(row.financials?.fees, row.financials?.currency)}</dd></div>
            <div><dt className="text-muted-foreground">Profit</dt><dd className={cn("mt-1 font-medium", row.profit == null ? "text-muted-foreground" : Number(row.profit) < 0 && "text-destructive-foreground")}><span className="sr-only">Profit: </span>{ledgerMoney(row.profit, row.financials?.currency)}</dd></div>
          </dl>
          <div className="flex flex-wrap gap-2">{row.conversation_id ? <Button size="xs" variant="ghost" onClick={() => onOpenListing(row.conversation_id!)}>Open item</Button> : null}
            <Button size="xs" variant="outline" onClick={() => { setSelected(row); setEditor((value) => value + 1); }}>Record amounts</Button></div>
        </li>)}</ul> : <EmptyRow>No sales match these filters. Widen the date range or clear filters.</EmptyRow>}
        <div className="flex flex-wrap items-center gap-2 text-xs"><span role="status">{data.summary.lines ? `${(page - 1) * 50 + 1}–${Math.min(page * 50, data.summary.lines)} of ${data.summary.lines} records` : "0 records"} · Page {page}</span><Button size="xs" variant="outline" disabled={page === 1 || query.isFetching} onClick={() => setPage(page - 1)}>Previous</Button><Button size="xs" variant="outline" disabled={!data.has_more || query.isFetching} onClick={() => setPage(page + 1)}>Next</Button></div>
      </PageSection>
    </> : null}
    {selected ? <FinancialEditor key={`${selected.id}:${editor}`} row={selected} onClose={() => setSelected(null)}
      onReload={(row) => { setSelected(row); setEditor((value) => value + 1); }} onSaved={() => {
        void queryClient.invalidateQueries({ queryKey: ["sales-ledger"] });
        void queryClient.invalidateQueries({ queryKey: ["marketplace-sales"] });
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        void queryClient.invalidateQueries({ queryKey: ["conversation", selected.conversation_id] });
        void queryClient.invalidateQueries({ queryKey: ["analytics"] });
        setSelected(null);
      }} /> : null}
  </>;
}

function FinancialEditor({ row, onClose, onSaved, onReload }: { row: SalesLedgerRow; onClose: () => void; onSaved: () => void; onReload: (row: SalesLedgerRow) => void }) {
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const [soldOn, setSoldOn] = useState(row.sold_at?.slice(0, 10) || "");
  const [currency, setCurrency] = useState(row.financials?.currency || "");
  const [values, setValues] = useState<Record<FinancialField, string>>(() => Object.fromEntries(FINANCIAL_FIELDS.map((field) => [field, row.financials?.[field] || ""])) as Record<FinancialField, string>);
  const save = useMutation({ mutationFn: () => api.salesLedger.save(row.id, {
    sold_on: soldOn || null,
    currency, ...Object.fromEntries(FINANCIAL_FIELDS.map((field) => [field, values[field].trim() || null])) as Omit<FinancialEntry, "currency">,
    expected_revision: row.financial_revision, expected_order_basis: row.order_basis,
  }), retry: false, onSuccess: onSaved });
  const reload = useMutation({ mutationFn: () => api.salesLedger.get(row.id), retry: false, onSuccess: onReload });
  const busy = save.isPending || reload.isPending;
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogPopup className="sm:max-w-xl"><DialogHeader className="shrink-0"><DialogTitle>Record sale amounts</DialogTitle><DialogDescription>{marketplaceName(row.marketplace)} order {row.order_id} · line {row.line_id} · quantity {row.quantity ?? "unknown"} · {row.order_status.replace(/_/g, " ")}</DialogDescription></DialogHeader>
      <form className="flex min-h-0 flex-col" onSubmit={(event) => { event.preventDefault(); if (!busy) save.mutate(); }}><DialogPanel className="space-y-4">
        <PageNote>Enter totals for this sale, excluding buyer taxes. Blank is unknown; zero is a confirmed zero. Net fees may be negative for fee credits. Saving records your figures in Anteroom; quantities and marketplace listings stay unchanged.</PageNote>
        {row.financials_source === "vendoo" ? <PageNote>These are imported historical sale details. Save corrections here in Anteroom.</PageNote> : null}
        {row.financials_stale ? <PageNote>This order changed. Review every amount before saving again.</PageNote> : null}
        <label className="block space-y-1 text-xs">Sale date (UTC)<Input type="date" min="1970-01-01" max={today} value={soldOn} disabled={busy} onChange={event => setSoldOn(event.target.value)} /></label>
        <label className="block space-y-1 text-xs">Currency code<Input required pattern="[A-Z]{3}" maxLength={3} value={currency} placeholder="USD" disabled={busy} onChange={(event) => setCurrency(event.target.value.toUpperCase())} /></label>
        <div className="grid gap-3 sm:grid-cols-2">{FINANCIAL_FIELDS.map((field) => <label key={field} className="space-y-1 text-xs">{LABELS[field]}<Input inputMode="decimal" value={values[field]} placeholder="Unknown" disabled={busy} onChange={(event) => setValues((previous) => ({ ...previous, [field]: event.target.value }))} /></label>)}</div>
        {save.error ? <PageError message={save.error.message} /> : null}{reload.error ? <PageError message={reload.error.message} /> : null}
      </DialogPanel><DialogFooter className="shrink-0"><Button type="button" variant="ghost" disabled={busy} onClick={() => reload.mutate()}>Reload record</Button><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Cancel</Button><Button type="submit" disabled={busy || !/^[A-Z]{3}$/.test(currency)}>{save.isPending ? "Saving…" : "Save amounts"}</Button></DialogFooter></form>
    </DialogPopup>
  </Dialog>;
}
