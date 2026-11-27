import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { DownloadIcon, RefreshCwIcon } from "lucide-react";
import { api } from "../../api/client";
import type { ManagedUsage, ManagedUsageService } from "../../api/types";
import { useWorkspaceRuntime } from "../../app/runtime";
import { cn } from "../../lib/utils";
import { downloadFile } from "../downloadFile";
import { PageError, PageSkeleton } from "../WorkspacePage";
import { Button } from "../ui/button";
import { Toggle, ToggleGroup } from "../ui/toggle-group";
import { SettingsNote, SettingsRow, SettingsSection, SettingsSelect } from "./SettingsLayout";
import { counted, dayLabel, formatCost, periodDates, periodLabel } from "./usageFormat";

const amount = "text-sm tabular-nums text-foreground";

export function UsageSettings() {
  const { account } = useWorkspaceRuntime();
  const [period, setPeriod] = useState("30d");
  const [exportError, setExportError] = useState("");
  const usage = useQuery({
    queryKey: ["managed-usage", account?.id, period],
    queryFn: () => api.settings.managedUsage(period),
    placeholderData: keepPreviousData,
  });
  const data = usage.data;
  const empty = !data || data.openrouter.count + data.photoroom.count === 0;
  // The picked period shows at once; the figures under it follow when they arrive.
  const stale = usage.isPlaceholderData;
  const periods = ["30d", ...new Set([...(data?.months ?? []), ...(period === "30d" ? [] : [period])])]
    .map((id) => ({ value: id, label: periodLabel(id) }));
  const download = () => {
    setExportError("");
    downloadFile(api.settings.managedUsageCsv(period), `anteroom-usage-${period}.csv`)
      .catch((cause: unknown) => setExportError(cause instanceof Error ? cause.message : "Couldn’t download this file."));
  };

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 sm:px-4">
        <SettingsSelect aria-label="Usage period" className="w-44" value={period} options={periods}
          onChange={(next) => { setExportError(""); setPeriod(next); }} />
        <div className="flex items-center gap-1">
          <Button variant="outline" size="sm" disabled={empty || stale} onClick={download}
            aria-label="Download CSV" title={empty ? "Nothing to download for this period" : "Every charge in this period"}>
            <DownloadIcon aria-hidden />
            <span className="max-sm:hidden">Download CSV</span>
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label="Refresh usage" title="Refresh usage"
            disabled={usage.isFetching} onClick={() => void usage.refetch()}>
            <RefreshCwIcon aria-hidden className={cn(usage.isFetching && "animate-spin")} />
          </Button>
        </div>
      </div>
      {exportError && <SettingsNote tone="error" className="px-3 sm:px-4">{exportError}</SettingsNote>}
      <SettingsSection id="managed-usage" title="Metered usage" hideTitle aria-busy={stale || undefined}
        className={cn("transition-opacity", stale && "opacity-60")}>
        {usage.isPending && <div className="p-3 sm:p-4"><PageSkeleton label="Loading usage" cards={2} /></div>}
        {usage.isError && !data && <div className="p-3 sm:p-4"><PageError message={`Couldn’t load usage. ${usage.error.message}`} /></div>}
        {data && <>
          <SettingsRow title="Total metered usage" className="wrap-anywhere" inlineControl
            description={<>{periodDates(data.period.start, data.period.end)}{account?.email ? ` · ${account.email}` : ""}</>}
            control={<strong className="text-2xl font-semibold tracking-tight tabular-nums">{formatCost(data.total_usd)}</strong>}
          >
            {empty
              ? <SettingsNote>No metered usage in this period. AI calls and photo edits made through Anteroom show up here.</SettingsNote>
              : <DailyCost days={data.days} />}
          </SettingsRow>
          {!empty && <>
            <ServiceRow name="OpenRouter" noun="AI call" unit="call" by="model" service={data.openrouter} />
            <ServiceRow name="Photoroom" noun="photo edit" unit="edit" by="kind of edit" service={data.photoroom} />
          </>}
          <p className="px-3 py-3 text-xs text-muted-foreground sm:px-4">
            Costs in USD cover Anteroom-managed services. Your own API keys are billed separately. Days and months are in UTC.
            {usage.dataUpdatedAt > 0 && ` Updated ${new Date(usage.dataUpdatedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`}
            {usage.isError && " The last refresh failed; these figures may be out of date."}
          </p>
        </>}
      </SettingsSection>
    </div>
  );
}

/** One service's total, then what it went on. The item rows add up to the total. */
function ServiceRow({ name, noun, unit, by, service }: {
  name: string;
  /** What one charge is, for the total ("AI call") and for each row ("call"). */
  noun: string;
  unit: string;
  by: string;
  service: ManagedUsageService;
}) {
  const tokens = (item: { input_tokens: number; output_tokens: number }) => {
    const total = item.input_tokens + item.output_tokens;
    return total > 0 ? ` · ${counted(total, "token")}` : "";
  };
  return (
    <SettingsRow title={name} description={`${counted(service.count, noun)}${tokens(service)}`} inlineControl
      control={<span className={amount}>{formatCost(service.usd)}</span>}
    >
      {service.items.length > 0 && (
        <ul className="space-y-1.5 text-xs" aria-label={`${name} by ${by}`}>
          {service.items.map((item) => (
            <li key={item.label} className="flex items-baseline gap-3">
              <span className="flex min-w-0 flex-1 flex-col gap-x-3 gap-y-0.5 @min-[32rem]/settings-row:flex-row @min-[32rem]/settings-row:justify-between">
                <span className="truncate text-foreground">{item.label || "Other"}</span>
                <span className="shrink-0 text-muted-foreground tabular-nums">
                  {counted(item.count, unit)}{tokens(item)}
                </span>
              </span>
              <span className="w-16 shrink-0 text-right text-foreground tabular-nums">{formatCost(item.usd)}</span>
            </li>
          ))}
        </ul>
      )}
    </SettingsRow>
  );
}

type DayView = "chart" | "table";

/** Cost per UTC day as bars, or the same numbers as a table. */
function DailyCost({ days }: { days: ManagedUsage["days"] }) {
  const [view, setView] = useState<DayView>("chart");
  const [active, setActive] = useState<number | null>(null);
  const totals = days.map((day) => day.openrouter + day.photoroom);
  const peak = Math.max(...totals, 0);
  const peakDay = `${dayLabel(days[totals.indexOf(peak)].date)}, ${formatCost(peak)}`;
  const picked = active == null ? null : days[active];
  const readout = picked == null || active == null
    ? `Highest day: ${peakDay}`
    : totals[active] === 0
      ? `${dayLabel(picked.date)}: no usage`
      : `${dayLabel(picked.date)}: ${formatCost(totals[active])}${picked.openrouter > 0 && picked.photoroom > 0
        ? ` (OpenRouter ${formatCost(picked.openrouter)}, Photoroom ${formatCost(picked.photoroom)})` : ""}`;
  const used = days.filter((_, index) => totals[index] > 0).reverse();

  return (
    <div className="flex flex-col gap-2">
      <div className="flex min-h-8 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="text-xs text-muted-foreground tabular-nums">{view === "chart" ? readout : "Cost per day"}</span>
        <ToggleGroup aria-label="Show cost per day as" value={[view]}
          onValueChange={(next) => { const value = next[0] as DayView | undefined; if (value) setView(value); }}>
          <Toggle value="chart">Chart</Toggle>
          <Toggle value="table">Table</Toggle>
        </ToggleGroup>
      </div>
      {view === "chart" ? (
        <div>
          <div role="img" aria-label={`Cost per day, ${dayLabel(days[0].date)} to ${dayLabel(days[days.length - 1].date)}. Highest day: ${peakDay}. Table has every day.`}
            className="flex h-28 items-end border-b border-border"
            onPointerLeave={(event) => { if (event.pointerType === "mouse") setActive(null); }}>
            {days.map((day, index) => (
              <div key={day.date} data-active={active === index || undefined}
                className="group/day flex h-full min-w-0 flex-1 items-end rounded-t-sm px-px data-active:bg-accent"
                onPointerEnter={() => setActive(index)}>
                <div className="w-full rounded-t-sm bg-primary/70 group-data-active/day:bg-primary"
                  style={{ height: `${peak > 0 ? (totals[index] / peak) * 100 : 0}%`, minHeight: totals[index] > 0 ? 2 : 0 }} />
              </div>
            ))}
          </div>
          <div aria-hidden className="mt-1 flex justify-between text-2xs text-muted-foreground tabular-nums">
            <span>{dayLabel(days[0].date)}</span>
            <span>{dayLabel(days[Math.floor(days.length / 2)].date)}</span>
            <span>{dayLabel(days[days.length - 1].date)}</span>
          </div>
        </div>
      ) : (
        <div className="max-h-72 overflow-y-auto">
          <table className="w-full text-xs">
            <caption className="sr-only">Cost per day, newest first. Days without usage are left out.</caption>
            <thead>
              <tr className="text-left text-muted-foreground">
                <th scope="col" className="pb-2 font-normal">Day</th>
                <th scope="col" className="pb-2 text-right font-normal">OpenRouter</th>
                <th scope="col" className="pb-2 text-right font-normal">Photoroom</th>
                <th scope="col" className="pb-2 text-right font-normal">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50 tabular-nums">
              {used.map((day) => (
                <tr key={day.date}>
                  <th scope="row" className="py-1.5 text-left font-normal text-foreground">{dayLabel(day.date)}</th>
                  <td className="py-1.5 text-right text-muted-foreground">{formatCost(day.openrouter)}</td>
                  <td className="py-1.5 text-right text-muted-foreground">{formatCost(day.photoroom)}</td>
                  <td className="py-1.5 text-right text-foreground">{formatCost(day.openrouter + day.photoroom)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
