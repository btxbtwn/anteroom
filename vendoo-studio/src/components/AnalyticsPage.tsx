import { SalesLedger } from "./SalesLedger";
import { WorkspaceTabs } from "./ebay/WorkspaceTabs";
import "./ebay/ebay.css";
import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronRightIcon, RefreshCwIcon } from "lucide-react";
import { api } from "../api/client";
import type { AnalyticsGroup, AnalyticsRange, InventoryAnalytics } from "../api/types";
import { labelDotClass, type LabelColor } from "../labelColors";
import { cn } from "../lib/utils";
import {
  ANALYTICS_RANGES, compactMoney, compareChange, dayRange, daysHint, formatChange, formatCurrency, formatDays,
  profitHint, revenueHint, SELL_THROUGH_HINT,
} from "./analyticsFormat";
import { MissingAmountsNotice } from "./MissingAmounts";
import { marketplaceName } from "./marketplaceNames";
import { Stat, StatGrid } from "./AnalyticsStat";
import { DiscountSuggestions } from "./DiscountSuggestions";
import { EtsyNotAllowed } from "./EtsyNotAllowed";
import { Input } from "./ui/input";
import { Button } from "./ui/button";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Toggle, ToggleGroup } from "./ui/toggle-group";
import {
  EmptyRow,
  ListRow,
  PageError,
  PageHeader,
  PageNote,
  PageSection,
  PageSkeleton,
  Panel,
  RowList,
  WorkspacePage,
} from "./WorkspacePage";

const PERIODS: { id: AnalyticsRange; label: string }[] = [
  ...ANALYTICS_RANGES,
  { id: "custom", label: "Custom" },
];

interface Props {
  onOpenListing: (id: string) => void;
  onOpenSalesRecords?: () => void;
}

export function AnalyticsPage({ onOpenListing }: Props) {
  const [view, setView] = useState("Sales records");
  const [missingOnly, setMissingOnly] = useState(false);
  const changeView = (next: string) => { setView(next); setMissingOnly(false); };
  return <WorkspacePage width="expanded"><h1 className="sr-only">Analytics</h1><WorkspaceTabs label="Analytics views" main items={["Sales records", "Inventory analytics"]} value={view} onChange={changeView}>
    {view === "Sales records"
      ? <SalesLedger key={String(missingOnly)} onOpenListing={onOpenListing} missingOnly={missingOnly} />
      : <InventoryAnalyticsView onOpenListing={onOpenListing} onOpenSalesRecords={() => { setView("Sales records"); setMissingOnly(true); }} />}
  </WorkspaceTabs></WorkspacePage>;
}

export function InventoryAnalyticsView({ onOpenListing, onOpenSalesRecords }: Props) {
  const [range, setRange] = useState<AnalyticsRange>("12m");
  const [breakdownSearch, setBreakdownSearch] = useState("");
  const [rankMetric, setRankMetric] = useState<RankMetric>("revenue");
  const [customStart, setCustomStart] = useState(() => isoDay(addDays(new Date(), -29)));
  const [customEnd, setCustomEnd] = useState(() => isoDay(new Date()));
  const custom = range === "custom";
  const customValid = customStart !== "" && customEnd !== "" && customStart <= customEnd;
  const query = useQuery({
    queryKey: ["analytics", range, custom ? customStart : null, custom ? customEnd : null],
    queryFn: () => api.analytics.get(range, custom ? { start: customStart, end: customEnd } : undefined),
    placeholderData: keepPreviousData,
    enabled: !custom || customValid,
  });
  const data = query.data;
  const heading = data?.range === "custom"
    ? customHeading(customStart, customEnd)
    : ANALYTICS_RANGES.find((item) => item.id === (data?.range ?? range))?.heading ?? "Sales";

  return (
    <>
      <PageHeader>
        {data ? <InventoryStrip inventory={data.inventory} /> : null}
        <div className="flex flex-wrap items-center gap-2">
          <Select value={range} onValueChange={value => { if (value) setRange(value as AnalyticsRange); }}>
            <SelectTrigger aria-label="Sales period" className="w-44"><SelectValue>{PERIODS.find(item => item.id === range)?.label}</SelectValue></SelectTrigger>
            <SelectPopup>{PERIODS.map(item => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectPopup>
          </Select>
          <Button size="icon-sm" variant="ghost" aria-label="Refresh analytics" title="Refresh analytics"
            disabled={query.isFetching || (custom && !customValid)} onClick={() => void query.refetch()}>
            <RefreshCwIcon aria-hidden className={cn(query.isFetching && "animate-spin")} />
          </Button>
          {query.isFetching && data ? <span className="sr-only" role="status">Updating analytics…</span> : null}
          {data ? <a href="#inventory-today" className="ml-auto text-xs font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">Review inventory today</a> : null}
        </div>
        {custom ? (
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <label className="block space-y-1">
              <span className="block">From</span>
              <Input
                size="compact"
                type="date"
                className="w-auto"
                value={customStart}
                max={customEnd || undefined}
                onChange={(event) => setCustomStart(event.target.value)}
              />
            </label>
            <label className="block space-y-1">
              <span className="block">To</span>
              <Input
                size="compact"
                type="date"
                className="w-auto"
                value={customEnd}
                min={customStart || undefined}
                onChange={(event) => setCustomEnd(event.target.value)}
              />
            </label>
            {!customValid ? <span>Pick a start date on or before the end date.</span> : null}
          </div>
        ) : null}
      </PageHeader>

      {query.isLoading && !data ? <PageSkeleton label="Loading analytics…" /> : null}
      {query.isError ? <PageError message={(query.error as Error).message || "Could not load analytics."} /> : null}

      {data && (!custom || customValid) ? (
        <>
          <PageSection aria-label={heading} title={heading}>
            <SalesStats data={data} />
            {data.previous ? <PageNote>Compared with {dayRange(data.previous.start, previousEnd(data.previous.end))}.</PageNote> : null}
            {data.undated_sales > 0 ? (
              <PageNote>
                {data.undated_sales === 1
                  ? `1 sale has no date and ${data.range === "all" ? "isn't charted" : "only counts in All time"}.`
                  : `${data.undated_sales} sales have no date and ${data.range === "all" ? "aren't charted" : "only count in All time"}.`}
              </PageNote>
            ) : null}
            {data.periods_truncated ? <PageNote>The chart shows the latest 18 months.</PageNote> : null}
            <SalesChart periods={data.periods} />
          </PageSection>

          <MissingAmountsNotice records={data.incomplete_sales.length} counts={data.missing_counts} scope="across all time"
            action={onOpenSalesRecords ? <Button size="sm" variant="outline" onClick={onOpenSalesRecords}>Show these sales</Button> : null} />

          <PageSection title="Sales breakdowns" description="Top six of each for this period.">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block space-y-1 text-xs">Search breakdowns<Input type="search" placeholder="Marketplace, category, brand or label" value={breakdownSearch} onChange={event => setBreakdownSearch(event.target.value)} /></label>
              <label className="block space-y-1 text-xs">Rank breakdowns by<Select value={rankMetric} onValueChange={value => { if (value) setRankMetric(value as RankMetric); }}>
                <SelectTrigger aria-label="Rank breakdowns by" className="w-full"><SelectValue>{RANK_METRICS.find(item => item.value === rankMetric)?.label}</SelectValue></SelectTrigger>
                <SelectPopup>{RANK_METRICS.map(item => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectPopup>
              </Select></label>
            </div>
            {breakdownSearch ? <Button size="sm" variant="ghost" onClick={() => setBreakdownSearch("")}>Clear breakdown search</Button> : null}
            <div className="grid gap-8 @3xl/page:grid-cols-2 @3xl/page:gap-6">
              <PerformanceList title="Marketplaces" rows={data.marketplaces} marketplace metric={rankMetric} search={breakdownSearch} />
              <PerformanceList title="Categories" rows={data.categories} metric={rankMetric} search={breakdownSearch} />
              <PerformanceList title="Brands" rows={data.brands} metric={rankMetric} search={breakdownSearch} />
              <PerformanceList title="Labels" rows={data.labels} metric={rankMetric} search={breakdownSearch} />
            </div>
          </PageSection>

          <PriceTiers tiers={data.price_tiers} />

          <PageSection
            id="inventory-today"
            className="scroll-mt-4"
            aria-label="Inventory today"
            title="Inventory today"
            description="Current active listings, across all sales periods."
          >
            <StatGrid className="grid-cols-2">
              <Stat label="Active listings" value={String(data.inventory.active)} />
              <Stat label="Asking value" value={formatCurrency(data.inventory.asking_value)} hint="Total asking prices; potential revenue" />
              <Stat label="Recorded inventory cost" value={data.inventory.cost_known ? formatCurrency(data.inventory.cost_value) : "—"} hint={`Cost recorded on ${data.inventory.cost_known} of ${data.inventory.active} active listings`} />
              <Stat label="Listed 90+ days" value={String(data.inventory.stale_count)} hint={`${formatCurrency(data.inventory.stale_value)} at asking prices`} />
            </StatGrid>
            {data.inventory.undated_count > 0 ? <PageNote>{data.inventory.undated_count} active {data.inventory.undated_count === 1 ? "listing has" : "listings have"} no list date and cannot be aged.</PageNote> : null}
          </PageSection>

          <DiscountSuggestions listings={data.stale} staleDays={data.stale_days} onOpenListing={onOpenListing} />

          <PageSection
            aria-label="Active listings"
            title="Current inventory by age"
            description="All active listings, regardless of the sales period. Open an age group to review its listings."
          >
            {data.aging.length === 0 ? (
              <EmptyRow>No active listings.</EmptyRow>
            ) : (
              <Panel>
                {data.aging.map((row) => (
                  <details key={row.label} className="group/age">
                    <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-2.5 text-sm outline-none transition-colors hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
                      <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground transition-transform group-open/age:rotate-90" />
                      <span className="min-w-0 flex-1 truncate text-foreground">{row.label}</span>
                      <span className="text-xs text-muted-foreground tabular-nums">{row.count} listings</span>
                      <span className="w-28 text-right text-foreground tabular-nums">{formatCurrency(row.asking_value)} asking</span>
                    </summary>
                    <ul className="divide-y divide-border/50 border-t border-border/50 bg-background/40">
                      {row.listings.map((listing) => (
                        <ListRow
                          key={listing.conversation_id}
                          title={listing.title}
                          meta={listing.days_listed == null ? "No list date" : formatDays(listing.days_listed)}
                          value={formatCurrency(listing.price)}
                          onClick={() => onOpenListing(listing.conversation_id)}
                        />
                      ))}
                    </ul>
                  </details>
                ))}
              </Panel>
            )}
          </PageSection>

          <EtsyNotAllowed onOpenListing={onOpenListing} />

          <PageSection
            aria-label="Oldest active listings"
            title="Review your oldest listings"
            description="Up to 8 listings that have been active for 90+ days. Open one to review its price, photos, or details."
          >
            {data.oldest.length === 0 ? <EmptyRow>No active listings with a recorded list date are 90+ days old.</EmptyRow> : (
              <RowList>
                {data.oldest.map((item) => (
                  <ListRow
                    key={item.conversation_id}
                    title={item.title}
                    meta={`${formatDays(item.days_listed)} listed`}
                    value={formatCurrency(item.price)}
                    onClick={() => onOpenListing(item.conversation_id)}
                  />
                ))}
              </RowList>
            )}
          </PageSection>

          <PageSection aria-label="Recent sales" title="Recent sales">
            {data.recent.length === 0 ? (
              <EmptyRow>No sales in this period.</EmptyRow>
            ) : (
              <RowList>
                {data.recent.map((sale) => (
                  <ListRow
                    key={sale.conversation_id}
                    title={sale.title}
                    meta={<>
                      {marketplaceName(sale.marketplace)}
                      {sale.sold_at ? ` · ${formatSold(sale.sold_at)}` : ""}
                      {sale.days_listed != null ? ` · ${formatDays(sale.days_listed)}` : ""}
                      {sale.event ? ` · ${sale.event}` : ""}
                    </>}
                    value={sale.price == null ? "Price unknown" : formatCurrency(sale.price)}
                    valueHint={sale.profit == null ? "Profit unavailable" : `${formatCurrency(sale.profit)} profit`}
                    onClick={() => onOpenListing(sale.conversation_id)}
                  />
                ))}
              </RowList>
            )}
          </PageSection>
        </>
      ) : null}
    </>
  );
}

function InventoryStrip({ inventory }: { inventory: InventoryAnalytics["inventory"] }) {
  const parts = [
    `${inventory.active} active`,
    `${formatCurrency(inventory.asking_value)} asking`,
  ];
  if (inventory.draft) parts.push(`${inventory.draft} draft${inventory.draft === 1 ? "" : "s"}`);
  if (inventory.sold) parts.push(`${inventory.sold} sold`);
  if (inventory.failed) parts.push(`${inventory.failed} failed`);
  if (inventory.working) parts.push(`${inventory.working} in progress`);
  return <p className="text-xs text-muted-foreground tabular-nums">Current inventory: {parts.join(" · ")}</p>;
}

/** Same names, order and wording as the figures on Sales records. */
function SalesStats({ data }: { data: InventoryAnalytics }) {
  const sales = data.sales;
  const previous = data.previous?.sales ?? null;
  return (
    <StatGrid className="grid-cols-2">
      <Stat label="Revenue" value={sales.count > 0 && sales.revenue_known === 0 ? null : formatCurrency(sales.revenue)} hint={revenueHint(sales)}
        change={compareChange(sales, previous, "revenue", sales.revenue, previous?.revenue ?? null, formatCurrency)} />
      <Stat
        label="Profit"
        value={sales.profit == null ? null : formatCurrency(sales.profit)}
        negative={sales.profit != null && sales.profit < 0}
        hint={profitHint(sales)}
        change={compareChange(sales, previous, "profit", sales.profit, previous?.profit ?? null, formatCurrency)}
      />
      <Stat
        label="Items sold"
        value={String(sales.count)}
        change={previous ? formatChange(sales.count, previous.count, String) : undefined}
        hint={sales.average_price == null ? undefined : `${formatCurrency(sales.average_price)} average sale`}
      />
      <Stat
        label="Median days to sell"
        value={sales.median_days == null ? null : formatDays(sales.median_days)}
        hint={daysHint(sales)}
        change={previous && sales.median_days != null && previous.median_days != null
          ? formatChange(sales.median_days, previous.median_days, formatDays) : undefined}
      />
      <Stat label="Sell-through" value={data.sell_through_rate == null ? null : `${data.sell_through_rate}%`} hint={SELL_THROUGH_HINT} />
      <Stat label="Profit margin" value={sales.margin == null ? null : `${sales.margin}%`} hint="Profit ÷ revenue, on sales with a cost"
        negative={sales.margin != null && sales.margin < 0} />
    </StatGrid>
  );
}

/** The API's previous period ends where this one starts; name its last whole day. */
function previousEnd(iso: string): string {
  const end = new Date(iso);
  end.setUTCMilliseconds(end.getUTCMilliseconds() - 1);
  return end.toISOString();
}

type ChartMetric = "revenue" | "count" | "profit";

function SalesChart({ periods }: { periods: InventoryAnalytics["periods"] }) {
  const [metric, setMetric] = useState<ChartMetric>("revenue");
  const peak = Math.max(...periods.map((period) => Math.abs(period[metric] ?? 0)), 0);
  const hasLoss = periods.some((period) => (period[metric] ?? 0) < 0);
  const baseline = hasLoss ? 110 : 0;
  const available = hasLoss ? 110 : 220;
  const metricName = metric === "count" ? "Items sold" : metric === "profit" ? "Profit" : "Revenue";
  const peakCount = Math.max(...periods.map((period) => period.count), 0);
  if (periods.length === 0 || peakCount === 0) {
    return <EmptyRow>No dated sales in this period.</EmptyRow>;
  }
  return (
    <Panel divided={false} className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">{metricName} over time</span>
        <ToggleGroup
          aria-label="Chart metric"
          value={[metric]}
          onValueChange={(next) => {
            const value = next[0] as ChartMetric | undefined;
            if (value) setMetric(value);
          }}
        >
          {(["revenue", "count", "profit"] as const).map((value) => (
            <Toggle key={value} value={value}>
              {value === "revenue" ? "Revenue" : value === "profit" ? "Profit" : "Items sold"}
            </Toggle>
          ))}
        </ToggleGroup>
      </div>
      {metric === "profit" ? <PageNote>Counts sales with a recorded cost; losses go below the line.</PageNote> : null}
      <div className="flex items-end gap-1 overflow-x-auto pb-1" role="list" aria-label={`${metricName} over time`}>
        {periods.map((period, index) => {
          const value = period[metric];
          const share = peak > 0 ? Math.abs(value ?? 0) / peak : 0;
          const height = share > 0 ? Math.max(Math.round(share * available), 2) : 0;
          const negative = (value ?? 0) < 0;
          const detail = `${period.label}: ${formatCurrency(period.revenue)}, ${saleCount(period.count)}, ${period.profit == null ? "profit unavailable" : `${formatCurrency(period.profit)} profit`}. Cost on ${period.profit_known} of ${period.count}; fees on ${period.fees_known} of ${period.profit_known}.`;
          return (
            <div
              key={`${period.label}-${index}`}
              className="group/bar flex min-w-9 flex-1 flex-col items-center gap-1.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
              role="listitem"
              tabIndex={0}
              aria-label={detail}
              title={detail}
            >
              <span className="text-2xs text-muted-foreground tabular-nums" aria-hidden="true">
                {value == null ? "—" : metric === "count" ? value : compactMoney(value)}
              </span>
              <div className="relative h-[220px] w-full" aria-hidden="true">
                <div className="absolute inset-x-0 border-t border-border" style={{ bottom: baseline }} />
                <div
                  className={cn(
                    "absolute inset-x-1 mx-auto max-w-16 rounded-sm transition-colors",
                    negative ? "bg-destructive/70 group-hover/bar:bg-destructive" : "bg-primary/70 group-hover/bar:bg-primary",
                  )}
                  style={{ height, bottom: negative ? baseline - height : baseline }}
                />
              </div>
              <span className="text-2xs text-muted-foreground">{period.label}</span>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

type RankMetric = "revenue" | "profit" | "count";
const RANK_METRICS: { value: RankMetric; label: string }[] = [
  { value: "revenue", label: "Revenue" },
  { value: "profit", label: "Profit" },
  { value: "count", label: "Items sold" },
];

function PerformanceList({ title, rows, marketplace = false, metric, search }: {
  metric: RankMetric;
  search: string;
  title: string;
  rows: (AnalyticsGroup & { color?: LabelColor | null })[];
  marketplace?: boolean;
}) {
  const ranked = rows.filter(row => (marketplace ? marketplaceName(row.id) : row.label).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())).sort((a, b) => {
    const first = a[metric];
    const second = b[metric];
    if (first == null) return second == null ? a.label.localeCompare(b.label) : 1;
    if (second == null) return -1;
    return second - first || a.label.localeCompare(b.label);
  }).slice(0, 6);
  return (
    <PageSection
      aria-label={title}
      title={title}
    >
      {ranked.length === 0 ? <EmptyRow>{search.trim() ? `No matching ${title.toLowerCase()}. Try another name or clear the search.` : `No ${title.toLowerCase()} recorded on sales in this period.`}</EmptyRow> : (
        <RowList>
          {ranked.map((row) => (
            <ListRow
              key={row.id}
              title={marketplace ? marketplaceName(row.id) : row.color ? (
                <span className="flex items-center gap-1.5">
                  <span aria-hidden className={cn("size-2 shrink-0 rounded-full", labelDotClass(row.color))} />
                  {row.label}
                </span>
              ) : row.label}
              meta={`${saleCount(row.count)}${row.median_days != null ? ` · ${formatDays(row.median_days)} median` : ""}`}
              value={metric === "count" ? row.count : row[metric] == null ? "—" : formatCurrency(row[metric])}
              valueHint={metric === "profit" ? (row.profit_known === 0 ? "Cost missing" : `Cost on ${row.profit_known}/${row.count} · fees on ${row.fees_known}/${row.profit_known}`) : undefined}
            />
          ))}
        </RowList>
      )}
    </PageSection>
  );
}

/** How this period's sales split across price bands, low to high. */
function PriceTiers({ tiers }: { tiers: AnalyticsGroup[] }) {
  const total = tiers.reduce((sum, tier) => sum + tier.count, 0);
  return (
    <PageSection
      aria-label="Sales by price"
      title="Sales by price"
      description={<>
        This period's sales with a recorded price, by what they sold for. A higher average sale means more of them in the
        top two bands, without them taking longer to sell.
      </>}
    >
      {total === 0 ? <EmptyRow>No priced sales in this period.</EmptyRow> : (
        <RowList>
          {tiers.map((tier) => (
            <li key={tier.id} className="flex min-w-0 flex-col gap-2 px-4 py-2.5 text-sm">
              <div className="flex min-w-0 items-center gap-4">
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-foreground">{tier.label}</span>
                  <span className="text-xs text-muted-foreground">
                    {saleCount(tier.count)} · {Math.round((tier.count / total) * 100)}%
                    {tier.median_days != null ? ` · ${formatDays(tier.median_days)} median` : ""}
                  </span>
                </span>
                <span className="flex shrink-0 flex-col items-end gap-0.5 text-right">
                  <span className="text-foreground tabular-nums">{formatCurrency(tier.revenue)}</span>
                  <span className="text-xs text-muted-foreground">{tier.profit == null ? "Profit unavailable" : `${formatCurrency(tier.profit)} profit`}</span>
                </span>
              </div>
              <div aria-hidden="true" className="h-1 overflow-hidden rounded-full bg-muted-foreground/12">
                <div className="h-full rounded-full bg-primary/70" style={{ width: `${(tier.count / total) * 100}%` }} />
              </div>
            </li>
          ))}
        </RowList>
      )}
    </PageSection>
  );
}

function saleCount(count: number): string {
  return `${count} sold`;
}

function formatSold(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function isoDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function customHeading(start: string, end: string): string {
  const format = (value: string) =>
    new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  return start === end ? format(start) : `${format(start)} – ${format(end)}`;
}
