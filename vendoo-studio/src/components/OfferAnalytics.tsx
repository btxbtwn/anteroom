import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { OfferAnalyticsResponse } from "../api/contract.gen";
import { marketplaceName } from "./marketplaceNames";
import { FormField, PageError, PageNote, PageSection, Panel } from "./WorkspacePage";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Skeleton } from "./ui/skeleton";

export function offerDates(period: string, now = new Date()) {
  const start = new Date(now);
  if (period === "week") start.setDate(start.getDate() - (start.getDay() + 6) % 7);
  if (period === "month") start.setDate(1);
  const day = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  return { start: day(start), end: day(now) };
}

export function OfferEvidence({ data }: { data: OfferAnalyticsResponse }) {
  return <Panel>
    {data.marketplaces.map((row) => <div key={row.marketplace} className="border-b border-border/60 p-3 last:border-0 sm:p-4">
      <div className="flex items-baseline justify-between gap-4 text-sm">
        <span className="font-medium">{marketplaceName(row.marketplace)}</span>
        <span className="tabular-nums">{row.confirmed_recipients == null ? "Not available" : `${row.confirmed_recipients} confirmed recipient offers`}</span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground tabular-nums">
        {row.marketplace === "poshmark" ? `${row.accepted_requests} accepted requests · recipients not reported`
          : row.marketplace === "depop" ? `${row.completed_jobs} completed bulk jobs · recipients not reported`
            : `${row.confirmed_sends} confirmed sends with recipient counts`}
      </p>
      <p className="mt-1 text-xs text-muted-foreground tabular-nums">
        {row.unknown} unknown · {row.unverified} unverified · {row.failed} failed · {row.pending} pending
      </p>
    </div>)}
  </Panel>;
}

export function OfferAnalytics() {
  const [period, setPeriod] = useState("today");
  const [custom, setCustom] = useState(offerDates("today"));
  const dates = period === "custom" ? custom : offerDates(period);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const query = useQuery({ queryKey: ["offer-analytics", dates.start, dates.end, timezone],
    queryFn: () => api.analytics.offers({ ...dates, timezone }),
    enabled: !!dates.start && !!dates.end && dates.start <= dates.end,
    staleTime: 60_000 });
  return <PageSection title="Offer activity" description="Marketplace evidence recorded by Anteroom, separate from automation runs.">
    <div className="mb-3 flex flex-wrap items-end gap-3">
      <FormField label="Period">
        <Select value={period} onValueChange={(value) => value && setPeriod(value)}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectPopup>{[["today", "Today"], ["week", "This week"], ["month", "This month"], ["custom", "Custom dates"]].map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectPopup>
        </Select>
      </FormField>
      {period === "custom" ? <>
        <FormField label="From"><Input type="date" value={custom.start} onChange={(e) => setCustom({ ...custom, start: e.target.value })} /></FormField>
        <FormField label="Through"><Input type="date" value={custom.end} onChange={(e) => setCustom({ ...custom, end: e.target.value })} /></FormField>
      </> : null}
    </div>
    <PageNote>{dates.start} – {dates.end} · {timezone} · Weeks start Monday.</PageNote>
    {dates.start > dates.end ? <PageError message="Choose a start on or before the end." /> : null}
    {query.error ? <PageError message={`Could not read offer activity: ${(query.error as Error).message}`} /> : null}
    {query.isLoading ? <Skeleton className="h-48" /> : query.data ? <OfferEvidence data={query.data} /> : null}
    <PageNote>Partial history: only recorded sends are included; previews, cooldown checks and skips are excluded. eBay history before send-date tracking is excluded. Counts are recipient offers, not unique buyers; repeat offers count again.</PageNote>
    <PageNote>Converted sales: Not available. Saved offers cannot be reliably linked to orders. A later sale of the same listing does not prove conversion.</PageNote>
  </PageSection>;
}
