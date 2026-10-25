import { useState, type ReactNode } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronRightIcon } from "lucide-react";
import { api } from "../api/client";
import type { AnalyticsGroup, AnalyticsRange, AnalyticsSales, InventoryAnalytics } from "../api/types";
import { cn } from "../lib/utils";
import { ANALYTICS_RANGES, formatChange, formatDays, formatMoney } from "./analyticsFormat";
import { marketplaceName } from "./marketplaceNames";
import { Stat, StatGrid } from "./AnalyticsStat";
import { DiscountSuggestions } from "./DiscountSuggestions";
import { EtsyNotAllowed } from "./EtsyNotAllowed";
import { VendooImportButton } from "./VendooImportButton";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Spinner } from "./ui/spinner";
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
}

export function AnalyticsPage({ onOpenListing }: Props) {
  const [range, setRange] = useState<AnalyticsRange>("12m");
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
    <WorkspacePage width="expanded">
      <PageHeader
        title="Analytics"
        description="Sales and inventory from the listings imported from Vendoo."
        actions={<VendooImportButton variant="analytics" />}
      >
        {data ? (
          <div className="flex flex-col gap-1 text-xs text-muted-foreground">
            <p>
              {data.last_updated_at ? <>
                {"Last updated on "}
                <time dateTime={data.last_updated_at}>{new Date(data.last_updated_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</time>
                {" from Vendoo."}
              </> : "Last updated on: not recorded yet. Resync Vendoo data to record an update."}
            </p>
            <InventoryStrip inventory={data.inventory} />
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-3">
          <ToggleGroup
            aria-label="Sales period"
            className="flex-wrap"
            value={[range]}
            onValueChange={(next) => {
              const value = next[0] as AnalyticsRange | undefined;
              if (value) setRange(value);
            }}
          >
            {PERIODS.map((item) => (
              <Toggle key={item.id} value={item.id}>{item.label}</Toggle>
            ))}
          </ToggleGroup>
          {query.isFetching && data ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
              <Spinner size="xs" aria-hidden role={undefined} />
              Updating analytics…
            </span>
          ) : null}
        </div>
        {custom ? (
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <label className="flex items-center gap-2">
              From
              <Input
                nativeInput
                size="compact"
                type="date"
                className="w-auto"
                value={customStart}
                max={customEnd || undefined}
                onChange={(event) => setCustomStart(event.target.value)}
              />
            </label>
            <label className="flex items-center gap-2">
              To
              <Input
                nativeInput
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
          {data.incomplete_sales.length > 0 ? (
            <PageSection
              aria-label="Missing sales data"
              className="rounded-xl border border-warning/32 bg-warning-surface p-4"
              title={`${data.incomplete_sales.length} ${data.incomplete_sales.length === 1 ? "sale needs" : "sales need"} sales data`}
              description={<>
                Across all time. Add the missing details in Vendoo, then resync to update your analytics.
                Missing prices, costs, or fees leave revenue and profit incomplete.
              </>}
            >
              <Disclosure summary="Review sales with missing data">
                <RowList className="max-h-80 overflow-y-auto">
                    {data.incomplete_sales.map((sale) => (
                      <ListRow
                        key={sale.conversation_id}
                        title={sale.title}
                        meta={`Missing ${sale.missing.join(", ")}`}
                        onClick={() => onOpenListing(sale.conversation_id)}
                      />
                    ))}
                </RowList>
              </Disclosure>
            </PageSection>
          ) : null}

          <PageSection aria-label={heading} title={heading}>
            <SalesStats data={data} />
            {data.previous ? (
              <PageNote>
                Compared with {formatSold(data.previous.start)} – {formatSold(data.previous.end)}
                {" (previous period, same length)."}
              </PageNote>
            ) : null}
            {data.sales.revenue_known < data.sales.count ? (
              <PageNote>
                {data.sales.count - data.sales.revenue_known} {data.sales.count - data.sales.revenue_known === 1 ? "sale has" : "sales have"} no recorded price.
                Revenue, averages, and breakdowns include recorded amounts only.
              </PageNote>
            ) : null}
            {data.undated_sales > 0 ? (
              <PageNote>
                {data.undated_sales} {data.undated_sales === 1 ? "sale has" : "sales have"} no date.
                {data.range === "all"
                  ? " Included in totals, but excluded from the chart."
                  : " Excluded from this period; included in All time."}
              </PageNote>
            ) : null}
            {data.periods_truncated ? <PageNote>The chart shows the latest 18 months.</PageNote> : null}
            <SalesChart periods={data.periods} />
          </PageSection>

          <div className="grid gap-8 @3xl/page:grid-cols-3 @3xl/page:gap-6">
            <PerformanceList title="Marketplaces" rows={data.marketplaces} marketplace />
            <PerformanceList title="Categories" rows={data.categories} />
            <PerformanceList title="Brands" rows={data.brands} />
          </div>

          <PriceTiers tiers={data.price_tiers} />

          <PageSection
            aria-label="Inventory today"
            title="Inventory today"
            description="Current active listings, across all sales periods."
          >
            <StatGrid>
              <Stat label="Active listings" value={String(data.inventory.active)} />
              <Stat label="Asking value" value={formatMoney(data.inventory.asking_value)} hint="Total asking prices; potential revenue" />
              <Stat label="Recorded inventory cost" value={data.inventory.cost_known ? formatMoney(data.inventory.cost_value) : "—"} hint={`Cost recorded on ${data.inventory.cost_known} of ${data.inventory.active} active listings`} />
              <Stat label="Listed 90+ days" value={String(data.inventory.stale_count)} hint={`${formatMoney(data.inventory.stale_value)} at asking prices`} />
            </StatGrid>
            {data.inventory.undated_count > 0 ? <PageNote>{data.inventory.undated_count} active {data.inventory.undated_count === 1 ? "listing has" : "listings have"} no list date and cannot be aged.</PageNote> : null}
          </PageSection>

          <DiscountSuggestions listings={data.stale} onOpenListing={onOpenListing} />

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
                      <span className="w-28 text-right text-foreground tabular-nums">{formatMoney(row.asking_value)} asking</span>
                    </summary>
                    <ul className="divide-y divide-border/50 border-t border-border/50 bg-background/40">
                      {row.listings.map((listing) => (
                        <ListRow
                          key={listing.conversation_id}
                          title={listing.title}
                          meta={listing.days_listed == null ? "No list date" : formatDays(listing.days_listed)}
                          value={formatMoney(listing.price)}
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
                    value={formatMoney(item.price)}
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
                    value={sale.price == null ? "Price unknown" : formatMoney(sale.price)}
                    valueHint={sale.profit == null ? "Profit unavailable" : `${formatMoney(sale.profit)} profit`}
                    onClick={() => onOpenListing(sale.conversation_id)}
                  />
                ))}
              </RowList>
            )}
          </PageSection>
        </>
      ) : null}
    </WorkspacePage>
  );
}

function Disclosure({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="group/disclosure flex flex-col">
      <summary className="inline-flex w-fit cursor-pointer list-none items-center gap-1 rounded-md text-xs font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon aria-hidden className="size-3.5 transition-transform group-open/disclosure:rotate-90" />
        {summary}
      </summary>
      <div className="mt-3">{children}</div>
    </details>
  );
}

function InventoryStrip({ inventory }: { inventory: InventoryAnalytics["inventory"] }) {
  const parts = [
    `${inventory.active} active`,
    `${formatMoney(inventory.asking_value)} asking`,
  ];
  if (inventory.draft) parts.push(`${inventory.draft} draft${inventory.draft === 1 ? "" : "s"}`);
  if (inventory.sold) parts.push(`${inventory.sold} sold`);
  if (inventory.failed) parts.push(`${inventory.failed} failed`);
  if (inventory.working) parts.push(`${inventory.working} in progress`);
  return <p className="text-foreground tabular-nums">Current inventory: {parts.join(" · ")}</p>;
}

function profitHint(sales: AnalyticsSales): string {
  if (sales.profit_known === 0) return "Record sale prices and costs to see profit";
  const scope =
    sales.profit_known === sales.count ? "" : ` on ${sales.profit_known} of ${sales.count} sales`;
  if (sales.fees_known === 0) return `After recorded costs and shipping${scope}. Fees missing`;
  if (sales.fees_known === sales.profit_known) return `After recorded cost, fees and shipping${scope}`;
  return `After recorded cost, fees and shipping${scope}. Fees recorded on ${sales.fees_known} of ${sales.profit_known}`;
}

function SalesStats({ data }: { data: InventoryAnalytics }) {
  const sales = data.sales;
  const profit = sales.profit;
  const previous = data.previous?.sales;
  const revenueComplete = previous && sales.revenue_known === sales.count && previous.revenue_known === previous.count;
  const profitComplete = previous && sales.profit_known === sales.count && previous.profit_known === previous.count
    && sales.fees_known === sales.count && previous.fees_known === previous.count;
  return (
    <StatGrid>
      <Stat label="Revenue" value={formatMoney(sales.revenue)}
        hint={`${sales.revenue_known} of ${sales.count} sales with recorded prices`}
        change={previous ? (revenueComplete ? formatChange(sales.revenue, previous.revenue, formatMoney) : "Comparison unavailable: missing prices") : undefined}
      />
      <Stat
        label="Sold"
        change={previous ? formatChange(sales.count, previous.count, String) : undefined}
        value={String(sales.count)}
        hint={sales.average_price == null ? undefined : `${formatMoney(sales.average_price)} average`}
      />
      <Stat
        label="Sell-through rate"
        value={data.sell_through_rate == null ? "—" : `${data.sell_through_rate}%`}
        hint="Sales in this period ÷ (sales in this period + active listings today)"
      />
      <Stat
        label="Profit"
        value={profit == null ? "—" : formatMoney(profit)}
        negative={profit != null && profit < 0}
        hint={profitHint(sales)}
        change={previous ? (profitComplete ? formatChange(profit, previous.profit, formatMoney) : "Comparison unavailable: missing costs or fees") : undefined}
      />
      <Stat label="Profit margin" value={sales.margin == null ? "—" : `${sales.margin}%`} hint="Profit ÷ revenue on sales with recorded cost" negative={sales.margin != null && sales.margin < 0} />
      <Stat
        label="Median time to sell"
        value={sales.median_days == null ? "—" : formatDays(sales.median_days)}
        hint={`Based on ${sales.days_known} of ${sales.count} sales`}
        change={previous ? formatChange(sales.median_days, previous.median_days, formatDays) : undefined}
      />
    </StatGrid>
  );
}

type ChartMetric = "revenue" | "count" | "profit";

function SalesChart({ periods }: { periods: InventoryAnalytics["periods"] }) {
  const [metric, setMetric] = useState<ChartMetric>("revenue");
  const peak = Math.max(...periods.map((period) => Math.abs(period[metric] ?? 0)), 0);
  const hasLoss = periods.some((period) => (period[metric] ?? 0) < 0);
  const baseline = hasLoss ? 110 : 0;
  const available = hasLoss ? 110 : 220;
  const metricName = metric === "count" ? "Sales" : metric === "profit" ? "Profit" : "Revenue";
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
              {value === "revenue" ? "Revenue" : value === "profit" ? "Profit" : "Sales"}
            </Toggle>
          ))}
        </ToggleGroup>
      </div>
      {metric === "profit" ? <PageNote>Profit uses sales with recorded cost and deducts recorded fees and shipping. Missing costs appear as —; loss bars extend below zero.</PageNote> : null}
      <div className="flex items-end gap-1 overflow-x-auto pb-1" role="list" aria-label={`${metricName} over time`}>
        {periods.map((period, index) => {
          const value = period[metric];
          const share = peak > 0 ? Math.abs(value ?? 0) / peak : 0;
          const height = share > 0 ? Math.max(Math.round(share * available), 2) : 0;
          const negative = (value ?? 0) < 0;
          const detail = `${period.label}: ${formatMoney(period.revenue)}, ${saleCount(period.count)}, ${period.profit == null ? "profit unavailable" : `${formatMoney(period.profit)} profit`}. Cost on ${period.profit_known} of ${period.count}; fees on ${period.fees_known} of ${period.profit_known}.`;
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
                {value == null ? "—" : metric === "count" ? value : formatMoney(value)}
              </span>
              <div className="relative h-[220px] w-full" aria-hidden="true">
                <div className="absolute inset-x-0 border-t border-border" style={{ bottom: baseline }} />
                <div
                  className={cn(
                    "absolute inset-x-1 rounded-sm transition-colors",
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
  { value: "count", label: "Sales" },
];

function PerformanceList({ title, rows, marketplace = false }: {
  title: string;
  rows: AnalyticsGroup[];
  marketplace?: boolean;
}) {
  const [metric, setMetric] = useState<RankMetric>("revenue");
  const ranked = [...rows].sort((a, b) => {
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
      actions={
        <Select value={metric} onValueChange={(value) => setMetric(value as RankMetric)}>
          <SelectTrigger aria-label={`Rank ${title.toLowerCase()} by`} size="compact" variant="ghost" className="w-auto min-w-0">
            <SelectValue>{RANK_METRICS.find((item) => item.value === metric)?.label}</SelectValue>
          </SelectTrigger>
          <SelectPopup align="end" alignItemWithTrigger={false}>
            {RANK_METRICS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
          </SelectPopup>
        </Select>
      }
    >
      {ranked.length === 0 ? <EmptyRow>No {title.toLowerCase()} recorded on sales in this period.</EmptyRow> : (
        <RowList>
          {ranked.map((row) => (
            <ListRow
              key={row.id}
              title={marketplace ? marketplaceName(row.id) : row.label}
              meta={`${saleCount(row.count)}${row.median_days != null ? ` · ${formatDays(row.median_days)} median` : ""}`}
              value={metric === "count" ? row.count : row[metric] == null ? "—" : formatMoney(row[metric])}
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
                    {saleCount(tier.count)} · {Math.round((tier.count / total) * 100)}% of sales
                    {tier.median_days != null ? ` · ${formatDays(tier.median_days)} median` : ""}
                  </span>
                </span>
                <span className="flex shrink-0 flex-col items-end gap-0.5 text-right">
                  <span className="text-foreground tabular-nums">{formatMoney(tier.revenue)}</span>
                  <span className="text-xs text-muted-foreground">{tier.profit == null ? "Profit unavailable" : `${formatMoney(tier.profit)} profit`}</span>
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
  return count === 1 ? "1 sale" : `${count} sales`;
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
