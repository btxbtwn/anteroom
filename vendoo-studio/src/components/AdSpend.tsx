import { useRef, useState, type FormEvent } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AdMarketplace, AdMarketplaceTotals, AdSpendEntry, AdSpendInput } from "../api/adSpend";
import type { AnalyticsRange } from "../api/types";
import { confirmDialog } from "../ui/confirmDialog";
import { ANALYTICS_RANGES, formatMoney } from "./analyticsFormat";
import { Stat, StatGrid } from "./AnalyticsStat";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Toggle, ToggleGroup } from "./ui/toggle-group";
import { EmptyRow, FormError, FormField, PageNote, PageSection, Panel, RowList } from "./WorkspacePage";

const PROGRAMS: Record<AdMarketplace, string> = { poshmark: "Promoted Closet", etsy: "Etsy Ads" };
const ADS_QUERY_KEY = ["ad-spend"];

/**
 * One marketplace’s recorded ad spend, set against its sales in the selected period.
 */
export function AdSpend({ marketplace }: { marketplace: AdMarketplace }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ADS_QUERY_KEY, queryFn: api.adSpend.list });
  const [range, setRange] = useState<AnalyticsRange>("12m");
  const totals = useQuery({
    queryKey: ["analytics", range],
    queryFn: () => api.analytics.get(range),
    placeholderData: keepPreviousData,
  });
  const entries = query.data?.filter(entry => entry.marketplace === marketplace);
  const marketTotals = totals.data?.ads.marketplaces.find(market => market.id === marketplace);

  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ADS_QUERY_KEY }),
    queryClient.invalidateQueries({ queryKey: ["analytics"] }),
  ]);
  const remove = useMutation({ mutationFn: api.adSpend.remove, onSuccess: refresh });
  const [editing, setEditing] = useState<AdSpendEntry | "new" | null>(null);
  const csvInput = useRef<HTMLInputElement>(null);
  const uploadCsv = useMutation({ mutationFn: api.adSpend.uploadEtsyCsv, onSuccess: refresh });

  const onRemove = async (entry: AdSpendEntry) => {
    const ok = await confirmDialog(
      `Remove ${formatMoney(entry.spend)} of ${PROGRAMS[entry.marketplace]} spend from ${formatRange(entry)}?`,
      { variant: "destructive", confirmLabel: "Remove" },
    );
    if (ok) remove.mutate(entry.id);
  };

  return (
    <PageSection
      aria-label="Ads"
      title="Ads"
      actions={editing == null ? <>
        {marketplace === "etsy" ? (
          <Button variant="outline" size="xs" disabled={uploadCsv.isPending} onClick={() => csvInput.current?.click()}>
            {uploadCsv.isPending ? "Importing…" : "Upload Etsy Ads CSV"}
          </Button>
        ) : null}
        <Button variant="outline" size="xs" onClick={() => setEditing("new")}>Add ad spend</Button>
      </> : null}
    >
      <input
        ref={csvInput}
        type="file"
        accept=".csv,text/csv"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) uploadCsv.mutate(file);
        }}
      />
      <PageNote>
        {marketplace === "etsy"
          ? "Download the Etsy Ads stats CSV and upload it here. Each day with activity becomes an entry; a newer export replaces those days. You can also record figures from your dashboard."
          : "Copy Promoted Closet spend from your Poshmark dashboard for a week or a month. You can include clicks, orders, and sales credited to the ads."}
      </PageNote>
      {uploadCsv.isSuccess ? (
        <PageNote role="status" className="text-foreground">
          Imported {uploadCsv.data.days} {uploadCsv.data.days === 1 ? "day" : "days"} of Etsy Ads,{" "}
          {formatMoney(uploadCsv.data.spend)} spent, {formatDay(uploadCsv.data.start)}–{formatDay(uploadCsv.data.end)}.
          {uploadCsv.data.overlapping_entries > 0
            ? ` ${uploadCsv.data.overlapping_entries === 1 ? "An Etsy entry you typed covers" : `${uploadCsv.data.overlapping_entries} Etsy entries you typed cover`} some of these days; remove ${uploadCsv.data.overlapping_entries === 1 ? "it" : "them"} so the spend is not counted twice.`
            : ""}
        </PageNote>
      ) : null}
      {uploadCsv.isError ? <FormError>{uploadCsv.error.message}</FormError> : null}
      {editing != null ? (
        <AdSpendForm
          key={editing === "new" ? "new" : editing.id}
          marketplace={marketplace}
          entry={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => { await refresh(); setEditing(null); }}
        />
      ) : null}

      {entries?.length ? (
        <ToggleGroup
          aria-label="Ad spend period"
          className="flex-wrap"
          value={[range]}
          onValueChange={(next) => {
            const value = next[0] as AnalyticsRange | undefined;
            if (value) setRange(value);
          }}
        >
          {ANALYTICS_RANGES.map((item) => <Toggle key={item.id} value={item.id}>{item.label}</Toggle>)}
        </ToggleGroup>
      ) : null}
      {marketTotals ? (
        <StatGrid>
          <MarketStat market={marketTotals} />
          <Stat label={`${marketplaceName(marketplace)} profit after ads`}
            value={marketTotals.profit_after_ads == null ? "—" : formatMoney(marketTotals.profit_after_ads)}
            negative={marketTotals.profit_after_ads != null && marketTotals.profit_after_ads < 0}
            hint={marketTotals.profit == null ? "Record sale prices and costs to see profit"
              : `${formatMoney(marketTotals.profit)} marketplace profit, less ${formatMoney(marketTotals.spend)} of ads`} />
        </StatGrid>
      ) : null}

      {query.isError ? <FormError>{(query.error as Error).message}</FormError> : null}
      {totals.isError ? <FormError>{(totals.error as Error).message}</FormError> : null}
      {remove.isError ? <FormError>{remove.error.message}</FormError> : null}
      {entries?.length === 0 && editing == null ? <EmptyRow>No ad spend recorded yet.</EmptyRow> : null}
      {Boolean(entries?.length) && !marketTotals ? <EmptyRow>No ad spend in this period.</EmptyRow> : null}
      {entries?.length ? (
        <RowList>
          {entries.map((entry) => (
            <li key={entry.id} className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-sm">
              {/* basis-56 lets the figures wrap under the name on a phone instead of crushing it. */}
              <span className="flex min-w-0 flex-1 basis-56 flex-col gap-0.5">
                <span className="text-foreground">{PROGRAMS[entry.marketplace]}</span>
                <span className="text-xs text-muted-foreground">
                  {formatRange(entry)}{entry.notes ? ` · ${entry.notes}` : ""}
                </span>
              </span>
              <span className="text-xs text-muted-foreground">{entryFigures(entry)}</span>
              <span className="w-20 text-right text-foreground tabular-nums max-[600px]:ms-auto">{formatMoney(entry.spend)}</span>
              <span className="flex items-center gap-1">
                <Button variant="ghost-muted" size="xs" onClick={() => setEditing(entry)}>Edit</Button>
                <Button variant="ghost-destructive" size="xs" disabled={remove.isPending} onClick={() => void onRemove(entry)}>Remove</Button>
              </span>
            </li>
          ))}
        </RowList>
      ) : null}
    </PageSection>
  );
}

function MarketStat({ market }: { market: AdMarketplaceTotals }) {
  const parts = [
    market.roas != null ? `${market.roas}× return on ad spend` : null,
    market.cost_per_click != null ? `${formatMoney(market.cost_per_click)} a click` : null,
    market.spend_percent != null
      ? `${market.spend_percent}% of ${formatMoney(market.sales_revenue)} ${marketplaceName(market.id)} sales`
      : `No ${marketplaceName(market.id)} sales recorded`,
    market.profit_after_ads != null
      ? `${formatMoney(market.profit_after_ads)} ${marketplaceName(market.id)} profit after ads`
      : null,
  ];
  return (
    <Stat
      label={PROGRAMS[market.id as AdMarketplace] ?? marketplaceName(market.id)}
      value={formatMoney(market.spend)}
      hint={parts.filter(Boolean).join(" · ")}
    />
  );
}

function AdSpendForm({
  marketplace,
  entry,
  onClose,
  onSaved,
}: {
  marketplace: AdMarketplace;
  entry: AdSpendEntry | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [start, setStart] = useState(entry?.start_date ?? "");
  const [end, setEnd] = useState(entry?.end_date ?? "");
  const [spend, setSpend] = useState(entry ? String(entry.spend) : "");
  const [clicks, setClicks] = useState(optional(entry?.clicks));
  const [orders, setOrders] = useState(optional(entry?.orders));
  const [revenue, setRevenue] = useState(optional(entry?.revenue));
  const [notes, setNotes] = useState(entry?.notes ?? "");
  const [error, setError] = useState("");
  const save = useMutation({
    mutationFn: (input: AdSpendInput) => entry ? api.adSpend.update(entry.id, input) : api.adSpend.create(input),
    onSuccess: onSaved,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (end < start) { setError("End date must be on or after the start date."); return; }
    save.mutate({
      marketplace,
      start_date: start,
      end_date: end,
      spend: Number(spend),
      clicks: clicks.trim() ? Number(clicks) : null,
      orders: orders.trim() ? Number(orders) : null,
      revenue: revenue.trim() ? Number(revenue) : null,
      notes,
    });
  }

  return (
    <Panel divided={false} className="p-4">
      <form className="flex flex-col gap-4" onSubmit={submit} aria-label={entry ? "Edit ad spend" : "Add ad spend"}>
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium text-foreground">{entry ? "Edit ad spend" : "Add ad spend"}</h3>
          <Button variant="ghost-muted" size="xs" disabled={save.isPending} onClick={onClose}>Close</Button>
        </div>
        <fieldset className="flex min-w-0 flex-col gap-4" disabled={save.isPending}>
          <div className="grid grid-cols-1 gap-3 @lg/page:grid-cols-2 @3xl/page:grid-cols-4">

            <FormField label="Start date"><Input nativeInput type="date" required value={start} onChange={(e) => setStart(e.target.value)} /></FormField>
            <FormField label="End date (inclusive)"><Input nativeInput type="date" required min={start} value={end} onChange={(e) => setEnd(e.target.value)} /></FormField>
            <FormField label="Spend ($)"><Input nativeInput type="number" required min={0} step="0.01" value={spend} onChange={(e) => setSpend(e.target.value)} /></FormField>
            <FormField label="Clicks"><Input nativeInput type="number" min={0} step="1" placeholder="Optional" value={clicks} onChange={(e) => setClicks(e.target.value)} /></FormField>
            <FormField label="Orders from ads"><Input nativeInput type="number" min={0} step="1" placeholder="Optional" value={orders} onChange={(e) => setOrders(e.target.value)} /></FormField>
            <FormField label="Sales from ads ($)"><Input nativeInput type="number" min={0} step="0.01" placeholder="Optional" value={revenue} onChange={(e) => setRevenue(e.target.value)} /></FormField>
          </div>
          <FormField label="Notes"><Textarea maxLength={2000} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></FormField>
          {error || save.isError ? <FormError>{error || save.error?.message}</FormError> : null}
          <div className="flex justify-end">
            <Button type="submit" size="sm" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
          </div>
        </fieldset>
      </form>
    </Panel>
  );
}

function entryFigures(entry: AdSpendEntry): string {
  return [
    entry.clicks != null ? `${entry.clicks} ${entry.clicks === 1 ? "click" : "clicks"}` : null,
    entry.orders != null ? `${entry.orders} ${entry.orders === 1 ? "order" : "orders"}` : null,
    entry.revenue != null ? `${formatMoney(entry.revenue)} sales` : null,
    entry.roas != null ? `${entry.roas}× ROAS` : null,
  ].filter(Boolean).join(" · ");
}

function formatDay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function formatRange(entry: Pick<AdSpendEntry, "start_date" | "end_date">): string {
  return entry.start_date === entry.end_date
    ? formatDay(entry.start_date)
    : `${formatDay(entry.start_date)} – ${formatDay(entry.end_date)}`;
}

function optional(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}
