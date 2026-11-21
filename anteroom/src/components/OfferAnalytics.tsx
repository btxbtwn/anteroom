import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ListIcon } from "lucide-react";
import { api } from "../api/client";
import type { OfferAnalyticsResponse } from "../api/contract.gen";
import { marketplaceName } from "./marketplaceNames";
import { OfferLog } from "./OfferLog";
import { offerProblems, offerReach, offersSent } from "./offerActivityFormat";
import { FormField, PageError, PageNote, PageSection, Panel } from "./WorkspacePage";
import { Button } from "./ui/button";
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
    {data.marketplaces.map((row) => {
      const sent = offersSent(row);
      const problems = offerProblems(row);
      return <div key={row.marketplace} role="group" aria-label={`${marketplaceName(row.marketplace)} offer activity`} className="p-3 sm:p-4">
        <div className="flex items-baseline justify-between gap-4 text-sm">
          <span className="font-medium">{marketplaceName(row.marketplace)}</span>
          <span className="tabular-nums">{sent === 1 ? "1 offer sent" : `${sent} offers sent`}</span>
        </div>
        {offerReach(row).map((line) => <p key={line} className="mt-1 text-xs text-muted-foreground tabular-nums">{line}</p>)}
        {problems ? <p className="mt-1 text-xs text-warning-foreground tabular-nums">{problems}</p> : null}
      </div>;
    })}
  </Panel>;
}

const OFFER_PERIODS = [
  { value: "today", label: "Today" }, { value: "week", label: "This week" },
  { value: "month", label: "This month" }, { value: "custom", label: "Custom dates" },
];

export function OfferAnalytics() {
  const [period, setPeriod] = useState("today");
  const [logOpen, setLogOpen] = useState(false);
  const [custom, setCustom] = useState(offerDates("today"));
  const dates = period === "custom" ? custom : offerDates(period);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const query = useQuery({ queryKey: ["offer-analytics", dates.start, dates.end, timezone],
    queryFn: () => api.analytics.offers({ ...dates, timezone }),
    enabled: !!dates.start && !!dates.end && dates.start <= dates.end,
    staleTime: 60_000 });
  return <PageSection aria-label="Offer activity" title="Offer activity"
    description="Offers Anteroom sent to likers and watchers, and what each marketplace confirmed about them."
    actions={<Button size="sm" variant="outline" onClick={() => setLogOpen(true)}><ListIcon />Offer log</Button>}>
    <div className="mb-3 flex flex-wrap items-end gap-3">
      <FormField label="Period">
        <Select value={period} onValueChange={(value) => value && setPeriod(value)}>
          <SelectTrigger><SelectValue>{OFFER_PERIODS.find(item => item.value === period)?.label}</SelectValue></SelectTrigger>
          <SelectPopup>{OFFER_PERIODS.map(({ value, label }) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectPopup>
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
    <PageNote>Each offer is one listing's offer to its likers or watchers, so one buyer can count more than once. Listings that were checked and skipped aren't counted, and neither are eBay offers from before Anteroom recorded send dates.</PageNote>
    <PageNote>Sales from offers aren't counted: marketplaces don't say which offer led to a sale.</PageNote>
    <OfferLog open={logOpen} onOpenChange={setLogOpen} />
  </PageSection>;
}
