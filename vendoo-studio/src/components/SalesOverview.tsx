import { useState } from "react";
import type { SalesOverview as Overview } from "../api/salesLedger";
import { cn } from "../lib/utils";
import { axisTicks, compactMoney, dayRange, formatCurrency, overviewFigures, weekLabel } from "./analyticsFormat";
import { Stat } from "./AnalyticsStat";
import { marketplaceName } from "./marketplaceNames";
import { PageNote, Panel } from "./WorkspacePage";
import { Toggle, ToggleGroup } from "./ui/toggle-group";

/** The figures and charts above Sales records, for the toolbar's period and marketplace. */
export function SalesOverview({ data, period, marketplaceChosen }: { data: Overview; period: string; marketplaceChosen: boolean }) {
  return (
    <section aria-label="Sales overview" className="flex flex-col gap-3">
      {/* The period sits by the figures: Inventory analytics may show another one. */}
      <h2 className="text-sm font-medium text-foreground">{period}</h2>
      <div className="grid grid-cols-2 gap-3 *:last:col-span-2 @3xl/page:grid-cols-5 @3xl/page:*:last:col-span-1">
        {overviewFigures(data.current, data.previous, marketplaceChosen).map((figure) => (
          <Stat key={figure.label} {...figure} />
        ))}
      </div>
      {data.previous ? (
        <PageNote>Compared with {dayRange(data.previous.start, data.previous.end)}.</PageNote>
      ) : null}
      <div className="grid gap-3 @3xl/page:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <WeeklyChart weeks={data.weeks} truncated={data.weeks_truncated} undated={data.undated_sales} />
        <MarketplaceBreakdown rows={data.marketplaces} />
      </div>
    </section>
  );
}

type WeekMetric = "count" | "revenue";

function WeeklyChart({ weeks, truncated, undated }: { weeks: Overview["weeks"]; truncated: boolean; undated: number }) {
  const [metric, setMetric] = useState<WeekMetric>("count");
  const [active, setActive] = useState<number | null>(null);
  const peak = Math.max(0, ...weeks.map((week) => week[metric]));
  const ticks = axisTicks(peak, metric === "count");
  const top = ticks[ticks.length - 1];
  const describe = (week: Overview["weeks"][number]) =>
    `Week of ${weekLabel(week.start)}: ${week.count} sold, ${
      week.revenue_known === 0 && week.count > 0 ? "revenue not recorded" : `${formatCurrency(week.revenue)} revenue`}`;
  const shown = active ?? (weeks.length ? weeks.length - 1 : null);
  // Label about six weeks along the axis, always including the last.
  const every = Math.max(1, Math.ceil(weeks.length / 6));
  const hasSales = weeks.some((week) => week.count > 0);
  return (
    <Panel divided={false} className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-foreground">{metric === "count" ? "Items sold" : "Revenue"} per week</h2>
        <ToggleGroup aria-label="Weekly chart shows" value={[metric]} onValueChange={(next) => {
          const value = next[0] as WeekMetric | undefined;
          if (value) setMetric(value);
        }}>
          <Toggle value="count">Items sold</Toggle>
          <Toggle value="revenue">Revenue</Toggle>
        </ToggleGroup>
      </div>
      {!hasSales ? (
        <p className="py-10 text-center text-xs text-muted-foreground">No dated sales in this period.</p>
      ) : (
        <>
          <p className="min-h-4 text-xs text-muted-foreground tabular-nums" aria-live="polite">
            {shown != null ? describe(weeks[shown]) : null}
          </p>
          <div className="flex gap-2">
            <div aria-hidden className="relative h-44 w-10 shrink-0 text-right text-2xs text-muted-foreground tabular-nums">
              {ticks.map((tick) => (
                <span key={tick} className="absolute right-0 translate-y-1/2" style={{ bottom: `${(tick / top) * 100}%` }}>
                  {metric === "count" ? tick : compactMoney(tick)}
                </span>
              ))}
            </div>
            <div className="min-w-0 flex-1">
              <div className="relative h-44">
                {ticks.map((tick) => (
                  <div key={tick} aria-hidden className="absolute inset-x-0 border-t border-border/60" style={{ bottom: `${(tick / top) * 100}%` }} />
                ))}
                <div role="list" aria-label={`${metric === "count" ? "Items sold" : "Revenue"} per week`} className="absolute inset-0 flex items-end gap-0.5"
                  onPointerLeave={() => setActive(null)}>
                  {weeks.map((week, index) => (
                    <div key={week.start} role="listitem" tabIndex={0} aria-label={describe(week)} title={describe(week)}
                      className="group/bar flex h-full min-w-0 flex-1 items-end justify-center rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onPointerEnter={() => setActive(index)} onFocus={() => setActive(index)} onBlur={() => setActive(null)}>
                      <div
                        className={cn("w-full max-w-6 rounded-t-[4px] transition-colors",
                          shown === index ? "bg-primary" : "bg-primary/60 group-hover/bar:bg-primary")}
                        style={{ height: week[metric] > 0 ? `max(2px, ${(week[metric] / top) * 100}%)` : 0 }}
                      />
                    </div>
                  ))}
                </div>
              </div>
              <div aria-hidden className="mt-1.5 flex gap-0.5 text-2xs text-muted-foreground">
                {weeks.map((week, index) => (
                  <span key={week.start} className="min-w-0 flex-1 overflow-visible whitespace-nowrap text-center">
                    {(weeks.length - 1 - index) % every === 0 ? weekLabel(week.start) : ""}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
      {truncated ? <PageNote>Showing the latest 52 weeks.</PageNote> : null}
      {undated > 0 ? <PageNote>{undated === 1 ? "1 sale has" : `${undated} sales have`} no date and {undated === 1 ? "isn't" : "aren't"} charted.</PageNote> : null}
    </Panel>
  );
}

function MarketplaceBreakdown({ rows }: { rows: Overview["marketplaces"] }) {
  const peak = Math.max(0, ...rows.map((row) => row.revenue));
  return (
    <Panel divided={false} className="flex flex-col gap-3 p-4">
      <h2 className="text-sm font-medium text-foreground">By marketplace</h2>
      {rows.length === 0 ? (
        <p className="py-10 text-center text-xs text-muted-foreground">No sales in this period.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th scope="col" className="pb-2 font-normal">Marketplace</th>
              <th scope="col" className="pb-2 text-right font-normal">Items sold</th>
              <th scope="col" className="pb-2 text-right font-normal">Revenue</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {rows.map((row) => (
              <tr key={row.id}>
                <th scope="row" className="py-2 pr-3 text-left font-normal">
                  <span className="block truncate text-foreground">{marketplaceName(row.id)}</span>
                  <span aria-hidden className="mt-1.5 block h-1 overflow-hidden rounded-full bg-muted-foreground/12">
                    <span className="block h-full rounded-full bg-primary/70" style={{ width: peak > 0 ? `${(row.revenue / peak) * 100}%` : 0 }} />
                  </span>
                </th>
                <td className="py-2 text-right align-top tabular-nums text-foreground">{row.count}</td>
                <td className="py-2 pl-3 text-right align-top tabular-nums text-foreground">
                  {row.revenue_known === 0 ? <span className="text-xs text-muted-foreground">Not recorded</span> : formatCurrency(row.revenue)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
