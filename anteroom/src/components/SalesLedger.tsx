import { cn } from "../lib/utils";
import { useDeferredValue, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DownloadIcon, ListFilterIcon, RefreshCwIcon } from "lucide-react";
import { api } from "../api/client";
import { lastThirtyDays, salesLedgerQuery, salesOverviewQuery } from "../api/pageQueries";
import { downloadFile } from "./downloadFile";
import { dayRange, formatCurrency } from "./analyticsFormat";
import { MissingAmountsNotice } from "./MissingAmounts";
import { SALE_FORM_FIELDS, type SaleFormField, type SalesLedgerPage, type SalesLedgerRow } from "../api/salesLedger";
import { pollMs } from "../api/polling";
import { marketplaceName } from "./marketplaceNames";
import { SalesOverview } from "./SalesOverview";
import { EmptyRow, PageError, PageHeader, PageNote, PageSection, PageSkeleton, Panel } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "./ui/menu";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { confirmDialog } from "../ui/confirmDialog";

const MARKETS = ["ebay", "etsy", "poshmark", "mercari", "depop", "other"];

export function ledgerMoney(value: string | null | undefined, currency: string | undefined): string {
  return value == null || !currency ? "Not recorded" : formatCurrency(value, currency);
}

const PERIOD_LABELS: Record<string, string> = { "30": "Last 30 days", "90": "Last 90 days", "365": "Last year", all: "All time", custom: "Custom dates" };

export function SalesLedgerSummary({ summary }: { summary: SalesLedgerPage["summary"] }) {
  // Dollar totals are in the figures above; other currencies keep their own.
  const other = summary.currencies.filter((group) => group.currency !== "USD");
  return <>
    <PageNote>{[
      summary.lines === 1 ? "1 sale record" : `${summary.lines} sale records`,
      summary.needs_review ? `${summary.needs_review} need review` : "",
      summary.unmatched ? `${summary.unmatched} need an inventory link` : "",
    ].filter(Boolean).join(" · ")}</PageNote>
    {other.length ? <div className="grid gap-3">
      {other.map((group) => <Panel key={group.currency} className="space-y-2 p-3" divided={false}>
        <p className="text-sm font-medium">Sales in {group.currency}</p>
        <dl className="grid grid-cols-2 gap-4 tabular-nums">
          <div><dt className="text-xs text-muted-foreground">Net revenue</dt><dd className={cn("mt-1", group.net_revenue == null ? "text-sm text-muted-foreground" : "text-xl font-semibold")}>{ledgerMoney(group.net_revenue, group.currency)}</dd><dd className="mt-1 text-xs text-muted-foreground">known on {group.net_known} of {group.lines} lines</dd></div>
          <div><dt className="text-xs text-muted-foreground">Recorded profit</dt><dd className={cn("mt-1", group.profit == null ? "text-sm text-muted-foreground" : "text-xl font-semibold")}>{ledgerMoney(group.profit, group.currency)}</dd><dd className="mt-1 text-xs text-muted-foreground">known on {group.profit_known} of {group.lines} lines</dd></div>
        </dl>
      </Panel>)}
    </div> : null}
  </>;
}

export function SalesLedger({ onOpenListing }: { onOpenListing: (id: string) => void }) {

  const queryClient = useQueryClient();
  const [initial] = useState(() => lastThirtyDays());
  const today = initial.end;
  const [start, setStart] = useState(initial.start);
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
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [exportError, setExportError] = useState("");
  const filters = { start, end, q, attention, sort, ...(marketplace !== "all" ? { marketplace } : {}) };
  const valid = Boolean(start && end && start >= "1970-01-01" && start <= end && end <= today);
  const query = useQuery({ ...salesLedgerQuery(filters, page),
    enabled: valid, refetchInterval: () => pollMs(15000) });
  const data = valid ? query.data : undefined;
  const allTime = period === "all";
  const overview = useQuery({
    ...salesOverviewQuery(allTime ? null : start, end, marketplace),
    enabled: valid, placeholderData: keepPreviousData, refetchInterval: () => pollMs(15000),
  });
  const filtered = search || attention !== "all" || marketplace !== "all" || sort !== "newest" || period !== "30";
  const clearFilters = () => { setSearch(""); setAttention("all"); setMarketplace("all"); setSort("newest"); setPeriod("30"); setEnd(today); setStart(new Date(Date.parse(today) - 29 * 86400000).toISOString().slice(0, 10)); setPage(1); };
  const refreshing = query.isFetching || overview.isFetching;
  const filterCount = [search, attention !== "all", marketplace !== "all", sort !== "newest"].filter(Boolean).length;
  const periodLabel = `${period === "custom" && valid ? dayRange(start, end) : PERIOD_LABELS[period]} · ${marketplace === "all" ? "All marketplaces" : marketplaceName(marketplace)}`;
  const exportCsv = (href: string, filename: string) => {
    setExportError("");
    downloadFile(href, filename).catch((cause: unknown) => setExportError(cause instanceof Error ? cause.message : "Could not download this export."));
  };
  const showMissing = (on: boolean) => { setAttention(on ? "missing" : "all"); setPage(1); };
  const refreshSales = (conversationId: string | null) => {
    void queryClient.invalidateQueries({ queryKey: ["sales-ledger"] });
    void queryClient.invalidateQueries({ queryKey: ["marketplace-sales"] });
    void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    void queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
    void queryClient.invalidateQueries({ queryKey: ["analytics"] });
  };
  // Only a sale the seller recorded can go: a marketplace's own sale would come back on its next check.
  const remove = useMutation({ mutationFn: (row: SalesLedgerRow) => api.salesLedger.remove(row.id, row.financial_revision),
    retry: false, onSuccess: (_result, row) => refreshSales(row.conversation_id) });
  const askRemove = async (row: SalesLedgerRow) => {
    const what = `${row.title || "this item"} on ${marketplaceName(row.marketplace)}, ${row.sold_at ? row.sold_at.slice(0, 10) : "no date"}`;
    if (await confirmDialog(`Delete this sale record?\nThe sale you recorded for ${what} is removed from Sales records and Analytics. Nothing changes on any marketplace.`,
      { variant: "destructive", confirmLabel: "Delete sale" })) remove.mutate(row);
  };
  return <>
    <PageHeader>
      {/* Phones: period, Filters and Export on one row so the figures come first. */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={period} onValueChange={value => {
          if (!value) return;
          setPeriod(value); setPage(1);
          if (value !== "custom") { setEnd(today); setStart(value === "all" ? "1970-01-01" : new Date(Date.parse(today) - (Number(value) - 1) * 86400000).toISOString().slice(0, 10)); }
        }}><SelectTrigger aria-label="Sales period" className="w-36 sm:w-40"><SelectValue>{PERIOD_LABELS[period]}</SelectValue></SelectTrigger><SelectPopup>
          {Object.entries(PERIOD_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
        </SelectPopup></Select>
        <Button size="sm" variant="outline" className="sm:hidden" aria-expanded={filtersOpen} aria-controls="sales-filters" onClick={() => setFiltersOpen(!filtersOpen)}>
          <ListFilterIcon aria-hidden />Filters{filterCount ? ` (${filterCount})` : ""}
        </Button>
        <div id="sales-filters" className={cn(filtersOpen ? "grid" : "hidden", "order-last w-full grid-cols-2 gap-2 sm:order-none sm:flex sm:w-auto sm:flex-1 sm:flex-wrap sm:items-center")}>
          <Input type="search" aria-label="Search sales" placeholder="Search item title or order number" className="col-span-2 min-w-0 sm:w-56 sm:flex-none" value={search} onChange={event => { setSearch(event.target.value); setPage(1); }} />
          <Select value={marketplace} onValueChange={(value) => { if (value) setMarketplace(value); setPage(1); }}>
            <SelectTrigger aria-label="Sales marketplace" className="w-full sm:w-44"><SelectValue>{marketplace === "all" ? "All marketplaces" : marketplaceName(marketplace)}</SelectValue></SelectTrigger>
            <SelectPopup><SelectItem value="all">All marketplaces</SelectItem>{MARKETS.map((market) => <SelectItem key={market} value={market}>{marketplaceName(market)}</SelectItem>)}</SelectPopup>
          </Select>
          <Select value={attention} onValueChange={value => { if (value) setAttention(value); setPage(1); }}>
            <SelectTrigger aria-label="Sales completeness" className="w-full sm:w-44"><SelectValue>{({ all: "All records", missing: "Missing sale data", review: "Needs review", loss: "Losses" } as Record<string, string>)[attention]}</SelectValue></SelectTrigger><SelectPopup>
              <SelectItem value="all">All records</SelectItem><SelectItem value="missing">Missing sale data</SelectItem><SelectItem value="review">Needs review</SelectItem><SelectItem value="loss">Losses</SelectItem>
            </SelectPopup></Select>
          <Select value={sort} onValueChange={value => { if (value) setSort(value); setPage(1); }}>
            <SelectTrigger aria-label="Sort sales" className="w-full sm:w-40"><SelectValue>{({ newest: "Newest first", oldest: "Oldest first", title: "Item title" } as Record<string, string>)[sort]}</SelectValue></SelectTrigger><SelectPopup><SelectItem value="newest">Newest first</SelectItem><SelectItem value="oldest">Oldest first</SelectItem><SelectItem value="title">Item title</SelectItem></SelectPopup>
          </Select>
          {filtered ? <Button size="sm" variant="ghost" className="col-span-2 justify-self-end" onClick={clearFilters}>Clear filters</Button> : null}
        </div>
        <div className="ml-auto flex items-center gap-1">
          <Button size="icon-sm" variant="ghost" aria-label="Refresh sales" title="Refresh sales" disabled={!valid || refreshing}
            onClick={() => { void query.refetch(); void overview.refetch(); }}>
            <RefreshCwIcon aria-hidden className={cn(refreshing && "animate-spin")} />
          </Button>
          <Menu>
            <MenuTrigger render={<Button size="sm" variant="outline" aria-label="Export CSV" />}>
              <DownloadIcon aria-hidden /><span className="hidden sm:inline">Export</span>
            </MenuTrigger>
            <MenuPopup align="end" width="content">
              <MenuItem disabled={!valid} onClick={() => exportCsv(api.salesLedger.salesCsv(filters), "anteroom-sales.csv")}>Export sales CSV</MenuItem>
              <MenuItem onClick={() => exportCsv(api.salesLedger.inventoryCsv(), "anteroom-inventory.csv")}>Export inventory CSV</MenuItem>
            </MenuPopup>
          </Menu>
        </div>
      </div>
      {exportError ? <PageError message={exportError} /> : null}
      {period === "custom" ? <div className="flex flex-wrap items-end gap-3 text-xs">
        <label className="block space-y-1">Sales from (UTC)<Input type="date" min="1970-01-01" max={end || today} value={start} onChange={(event) => { setStart(event.target.value); setPage(1); }} /></label>
        <label className="block space-y-1">Through (UTC)<Input type="date" min={start || "1970-01-01"} max={today} value={end} onChange={(event) => { setEnd(event.target.value); setPage(1); }} /></label>
      </div> : null}
    </PageHeader>
    {valid && overview.data ? <SalesOverview data={overview.data} period={periodLabel} marketplaceChosen={marketplace !== "all"} /> : null}
    {valid && overview.error ? <PageError message={overview.error.message} /> : null}
    {!valid ? <PageNote>Choose a valid UTC date range through today.</PageNote> : query.isPending ? <PageSkeleton label="Loading sales records…" /> : null}
    {query.error ? <PageError message={query.error.message} /> : null}
    {data ? <>
      <MissingAmountsNotice records={data.summary.missing_records} counts={data.summary.missing_counts}
        action={<Button size="sm" variant="outline" onClick={() => showMissing(attention !== "missing")}>
          {attention === "missing" ? "Show all sales" : "Show these sales"}</Button>} />
      <SalesLedgerSummary summary={data.summary} />
      {remove.error ? <PageError message={remove.error.message} /> : null}
      <PageSection title="Sales records" description="Record totals after discounts; leave an amount blank if unknown.">
        {data.rows.length ? <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/60 bg-card/40">{data.rows.map((row) => <li key={row.id} className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 text-sm">
          <div className="flex min-w-0 flex-1 basis-48 flex-col gap-0.5 break-words"><p className="font-medium">{row.title || `${marketplaceName(row.marketplace)} order ${row.order_id}`}</p>
            <p className="text-xs text-muted-foreground">{row.sold_at ? row.sold_at.slice(0, 10) : "Sale date missing"} · {marketplaceName(row.marketplace)} · {row.order_status.replace(/_/g, " ")} · quantity {row.quantity ?? "unknown"}</p>
            {row.missing_sale_data?.length ? <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-warning" />
              Missing {row.missing_sale_data.join(", ")}</p> : null}
            <details className="text-2xs text-muted-foreground"><summary className="cursor-pointer">Order details</summary><p className="font-mono">Order {row.order_id} · line {row.line_id}</p></details>
            {row.financials_stale ? <p className="text-xs text-warning-foreground">Order changed or recorded amounts need review.</p> : null}
          </div>
          <dl className="grid w-full grid-cols-3 gap-4 text-right text-xs tabular-nums sm:w-72">
            <div><dt className="text-muted-foreground">Sale price</dt><dd className="mt-1">{ledgerMoney(row.financials?.item_revenue, row.financials?.currency)}</dd></div>
            <div><dt className="text-muted-foreground">Fees</dt><dd className="mt-1">{ledgerMoney(row.financials?.fees, row.financials?.currency)}</dd></div>
            <div><dt className="text-muted-foreground">Profit</dt><dd className={cn("mt-1 font-medium", row.profit == null ? "text-muted-foreground" : Number(row.profit) < 0 && "text-destructive-foreground")}><span className="sr-only">Profit: </span>{ledgerMoney(row.profit, row.financials?.currency)}</dd></div>
          </dl>
          <div className="flex flex-wrap gap-2">{row.conversation_id ? <Button size="xs" variant="ghost" onClick={() => onOpenListing(row.conversation_id!)}>Open item</Button> : null}
            <Button size="xs" variant="outline" onClick={() => { setSelected(row); setEditor((value) => value + 1); }}>Record amounts</Button>
            {row.source === "seller" ? <Button size="xs" variant="ghost-destructive" disabled={remove.isPending && remove.variables?.id === row.id}
              onClick={() => void askRemove(row)}>{remove.isPending && remove.variables?.id === row.id ? "Deleting…" : "Delete"}</Button> : null}</div>
        </li>)}</ul> : <EmptyRow>No sales match these filters. Widen the date range or clear filters.</EmptyRow>}
        <div className="flex flex-wrap items-center gap-2 text-xs"><span role="status">{data.summary.lines ? `${(page - 1) * 50 + 1}–${Math.min(page * 50, data.summary.lines)} of ${data.summary.lines} records` : "0 records"} · Page {page}</span><Button size="xs" variant="outline" disabled={page === 1 || query.isFetching} onClick={() => setPage(page - 1)}>Previous</Button><Button size="xs" variant="outline" disabled={!data.has_more || query.isFetching} onClick={() => setPage(page + 1)}>Next</Button></div>
      </PageSection>
    </> : null}
    {selected ? <FinancialEditor key={`${selected.id}:${editor}`} row={selected} onClose={() => setSelected(null)}
      onReload={(row) => { setSelected(row); setEditor((value) => value + 1); }} onSaved={() => {
        refreshSales(selected.conversation_id);
        setSelected(null);
      }} /> : null}
  </>;
}

function FinancialEditor({ row, onClose, onSaved, onReload }: { row: SalesLedgerRow; onClose: () => void; onSaved: () => void; onReload: (row: SalesLedgerRow) => void }) {
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const [soldOn, setSoldOn] = useState(row.sold_at?.slice(0, 10) || "");
  const currency = row.financials?.currency || "USD";
  const [values, setValues] = useState<Record<SaleFormField, string>>(() => Object.fromEntries(SALE_FORM_FIELDS.map(([field]) => [field, row.financials?.[field] || ""])) as Record<SaleFormField, string>);
  // Refunds stay as recorded by the marketplace.
  const save = useMutation({ mutationFn: () => api.salesLedger.save(row.id, {
    sold_on: soldOn || null, currency,
    ...Object.fromEntries(SALE_FORM_FIELDS.map(([field]) => [field, values[field].trim().replace(/^\$/, "") || null])),
    expected_revision: row.financial_revision, expected_order_basis: row.order_basis,
  }), retry: false, onSuccess: onSaved });
  const reload = useMutation({ mutationFn: () => api.salesLedger.get(row.id), retry: false, onSuccess: onReload });
  const busy = save.isPending || reload.isPending;
  const refunds = row.financials?.refunds;
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogPopup className="sm:max-w-xl"><DialogHeader className="shrink-0"><DialogTitle>Record sale amounts</DialogTitle><DialogDescription>Sold on {marketplaceName(row.marketplace)} · quantity {row.quantity ?? "unknown"} · {row.order_status.replace(/_/g, " ")}</DialogDescription></DialogHeader>
      <form className="flex min-h-0 flex-col" onSubmit={(event) => { event.preventDefault(); if (!busy) save.mutate(); }}><DialogPanel className="space-y-4">
        {row.financials_stale ? <PageNote>This order changed. Review every amount before saving again.</PageNote> : null}
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs">Date<Input nativeInput type="date" min="1970-01-01" max={today} value={soldOn} disabled={busy} onChange={event => setSoldOn(event.target.value)} /></label>
          {SALE_FORM_FIELDS.map(([field, label]) => <label key={field} className="space-y-1 text-xs">{label}<Input inputMode="decimal" value={values[field]} placeholder="0.00" disabled={busy} onChange={(event) => setValues((previous) => ({ ...previous, [field]: event.target.value }))} /></label>)}
        </div>
        <PageNote>Cost of goods applies to this sale.{refunds != null && Number(refunds) !== 0 ? ` Refunded: ${ledgerMoney(refunds, currency)}.` : ""} Leave an amount blank if you don't know it. Nothing changes on any marketplace.</PageNote>
        {save.error ? <PageError message={save.error.message} /> : null}{reload.error ? <PageError message={reload.error.message} /> : null}
      </DialogPanel><DialogFooter className="shrink-0"><Button type="button" variant="ghost" disabled={busy} onClick={() => reload.mutate()}>Reload record</Button><Button type="button" variant="outline" disabled={busy} onClick={onClose}>Cancel</Button><Button type="submit" disabled={busy}>{save.isPending ? "Saving…" : "Save amounts"}</Button></DialogFooter></form>
    </DialogPopup>
  </Dialog>;
}
