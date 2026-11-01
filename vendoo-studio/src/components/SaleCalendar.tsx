import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SaleCalendarItem, SaleEvent, SaleEventStatus, SaleMarketplace, SalePattern, SalePlan, SaleRecord } from "../api/saleCalendar";
import { confirmDialog } from "../ui/confirmDialog";
import { formatMoney } from "./analyticsFormat";
import { marketplaceName } from "./marketplaceNames";
import { DISCOUNT_CEILING, calendarDate, dateOnly, monthDays, saleProfit, shiftMonth, suggestDiscount } from "./saleCalendarDates";
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { useVendooEnabled } from "../vendooMode";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Skeleton } from "./ui/skeleton";
import { Textarea } from "./ui/textarea";
import { EmptyRow, FormError, FormField, PageNote, PageSection, Panel } from "./WorkspacePage";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
type CalendarSave = { kind: "plan"; input: SalePlan } | { kind: "record"; input: SaleRecord };
type FormSeed = { start: string; end: string; marketplace: string; event?: SaleEvent; record?: boolean };

/** Each marketplace's chip tint, from the shared marketplace colors. */
const CHIP_TONE: Record<string, string> = {
  ebay: "bg-[color-mix(in_srgb,var(--color-ebay)_16%,transparent)]",
  depop: "bg-[color-mix(in_srgb,var(--color-depop)_16%,transparent)]",
  etsy: "bg-[color-mix(in_srgb,var(--color-etsy)_16%,transparent)]",
  sellwild: "bg-success/16",
};

function chipClass(marketplace: string, className?: string): string {
  return cn("rounded-sm px-1.5 py-0.5 text-3xs leading-snug text-foreground wrap-anywhere", CHIP_TONE[marketplace] ?? "bg-muted-foreground/12", className);
}

/** A disclosure of listings with a link to open each one. */
function ListingDetails({ summary, open, rows, onOpenListing }: {
  summary: string;
  open?: boolean;
  rows: { id: string; title: string; detail: string }[];
  onOpenListing: (id: string) => void;
}) {
  return (
    <details open={open} className="group/items text-xs">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon aria-hidden className="size-3.5 transition-transform group-open/items:rotate-90" />
        {summary}
      </summary>
      <ul className="mt-2 divide-y divide-border/50">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1.5">
            <button type="button" className="cursor-pointer text-left text-primary hover:underline" onClick={() => onOpenListing(row.id)}>{row.title}</button>
            <span className="text-muted-foreground">{row.detail}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function displayDate(day: string): string {
  return dateOnly(day).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** The suggestion a new plan opens with: no listing below a $0 floor, using this marketplace's history. */
function patternDiscount(pattern: SalePattern | undefined, items: SaleCalendarItem[]): number | null {
  if (pattern?.fee_percent == null) return null;
  return suggestDiscount(items.filter(item => item.marketplaces.includes(pattern.marketplace)), pattern.fee_percent, pattern.shipping_cost ?? 0, 0);
}

export function SaleCalendar({ marketplace: market, onOpenListing }: { marketplace: SaleMarketplace; onOpenListing: (id: string) => void }) {
  const vendooOn = useVendooEnabled();
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const [today, setToday] = useState(() => calendarDate(new Date()));
  useEffect(() => {
    const timer = window.setInterval(() => setToday(calendarDate(new Date())), 60000);
    return () => window.clearInterval(timer);
  }, []);
  const [month, setMonth] = useState(today.slice(0, 7));
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [form, setForm] = useState<FormSeed | null>(null);
  const [notice, setNotice] = useState("");
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["sale-calendar", timezone], queryFn: () => api.saleCalendar.get(timezone) });
  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: SaleEventStatus }) => api.saleCalendar.status(id, status),
    onSuccess: () => Promise.all([queryClient.invalidateQueries({ queryKey: ["sale-calendar"] }), queryClient.invalidateQueries({ queryKey: ["analytics"] })]),
  });
  const deleteMutation = useMutation({
    mutationFn: api.saleCalendar.remove,
    onSuccess: () => Promise.all([queryClient.invalidateQueries({ queryKey: ["sale-calendar"] }), queryClient.invalidateQueries({ queryKey: ["analytics"] })]),
  });
  const busy = statusMutation.isPending || deleteMutation.isPending;
  const patterns = (query.data?.patterns ?? []).filter(pattern => pattern.marketplace === market);
  const events = (query.data?.events ?? []).filter(event => event.marketplace === market);
  const visible = events.filter(event => selectedDay
    ? event.start_date <= selectedDay && event.end_date >= selectedDay
    : event.start_date <= monthDays(month)[41]! && event.end_date >= `${month}-01`);
  const activeEvents = events.filter(event => event.status !== "cancelled");

  function newPlan(start = selectedDay ?? today, end = start, marketplace: SaleMarketplace = market) {
    setForm({ start, end, marketplace });
    setMonth(start.slice(0, 7));
    setNotice("");
  }

  async function remove(event: SaleEvent) {
    if (await confirmDialog(`Delete “${event.title}” from your calendar?\nThis removes its local promotion history.`, { variant: "destructive", confirmLabel: "Delete event" })) {
      deleteMutation.mutate(event.id);
    }
  }

  return (
    <PageSection
      aria-label="Sale calendar"
      title="Sale calendar"
      description="Plan, run the sale on the marketplace, then mark it as run here. Saving a plan only adds it to this calendar."
      actions={<>
        <Button variant="ghost-muted" size="xs" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh calendar</Button>
        <Button variant="outline" size="xs" disabled={!query.data} onClick={() => setForm({ start: today, end: today, marketplace: market, record: true })}>Record a sale I ran</Button>
        <Button size="xs" disabled={!query.data} onClick={() => newPlan()}>Plan a sale</Button>
      </>}
    >
      {query.isLoading ? (
        <div className="flex flex-col gap-3" role="status" aria-label="Loading sale calendar…">
          <div className="grid gap-3 @2xl/page:grid-cols-3">
            <Skeleton shape="card" className="h-36" /><Skeleton shape="card" className="h-36" /><Skeleton shape="card" className="h-36" />
          </div>
          <Skeleton shape="card" className="h-72" />
        </div>
      ) : null}
      {query.isError ? <FormError>{query.error.message}</FormError> : null}
      {statusMutation.isError || deleteMutation.isError ? <FormError>{(statusMutation.error ?? deleteMutation.error)?.message}</FormError> : null}
      {notice ? <PageNote role="status" className="text-foreground">{notice}</PageNote> : null}
      {query.data ? <>
        {patterns.length ? <div className="grid gap-3 @2xl/page:grid-cols-2 @4xl/page:grid-cols-3">
          {patterns.map(pattern => {
            const discount = patternDiscount(pattern, query.data.items);
            return <Panel divided={false} key={pattern.marketplace} className="flex flex-col gap-3 p-4">
              <h3 className="text-sm font-medium text-foreground">{marketplaceName(pattern.marketplace)}</h3>
              <p className="text-xs leading-relaxed text-muted-foreground">{pattern.reason}</p>
              <div className="grid grid-cols-7 gap-1" aria-label={`${marketplaceName(pattern.marketplace)} sales by weekday`}>
                {pattern.weekdays.map((day, index) => (
                  <div key={day.label} className="flex flex-col items-center gap-1 rounded-md bg-muted-foreground/8 py-1.5" title={`${day.label}: ${day.count} sales; ${day.average ?? "—"} per week`}>
                    <span className="text-3xs text-muted-foreground">{WEEKDAYS[index]}</span>
                    <strong className="text-sm font-medium text-foreground tabular-nums">{day.count}</strong>
                  </div>
                ))}
              </div>
              {pattern.weeks > 0 ? <small className="text-xs text-muted-foreground">{pattern.sales} dated sales · {displayDate(pattern.history_start)}–{displayDate(pattern.history_end)}</small> : null}
              <Button variant="outline" size="xs" className="mt-auto self-start" onClick={() => newPlan(pattern.suggested_start, pattern.suggested_end, pattern.marketplace)}>Try {displayDate(pattern.suggested_start)}{pattern.suggested_end === pattern.suggested_start ? "" : `–${displayDate(pattern.suggested_end)}`}{discount == null ? "" : ` · ${discount}% off`}</Button>
            </Panel>;
          })}
        </div> : null}
        <PageNote>Suggestions come from your imported sales ({timezone}). They’re experiments, not a forecast of extra sales.</PageNote>
        <Panel divided={false} className="flex flex-col gap-3 p-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="icon-xs" aria-label="Previous month" onClick={() => { setMonth(shiftMonth(month, -1)); setSelectedDay(null); }}><ChevronLeftIcon /></Button>
              <h3 className="min-w-36 text-center text-sm font-medium text-foreground">{dateOnly(`${month}-01`).toLocaleDateString(undefined, { month: "long", year: "numeric" })}</h3>
              <Button variant="ghost" size="icon-xs" aria-label="Next month" onClick={() => { setMonth(shiftMonth(month, 1)); setSelectedDay(null); }}><ChevronRightIcon /></Button>
              <Button variant="outline" size="xs" className="ms-1" onClick={() => { setMonth(today.slice(0, 7)); setSelectedDay(today); }}>Today</Button>
            </div>

          </div>
          <div className="grid grid-cols-7 gap-1" role="group" aria-label="Promotion dates">
            {WEEKDAYS.map(day => <div className="py-1 text-center text-2xs text-muted-foreground" key={day}>{day}</div>)}
            {monthDays(month).map(day => {
              const onDay = activeEvents.filter(event => event.start_date <= day && event.end_date >= day);
              const outside = day.slice(0, 7) !== month;
              return <button key={day} type="button" aria-pressed={selectedDay === day} aria-label={`${displayDate(day)}, ${onDay.length} sale events`} aria-current={day === today ? "date" : undefined}
                className={cn(
                  "flex min-h-18 cursor-pointer flex-col items-stretch gap-1 rounded-lg border border-border/60 p-1.5 text-left outline-none transition-colors hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring @3xl/page:min-h-23 @3xl/page:p-2",
                  outside && "opacity-50",
                  day === selectedDay && "border-primary/60 bg-primary/8",
                )}
                onClick={() => { setSelectedDay(day); if (outside) setMonth(day.slice(0, 7)); }}>
                <span className={cn("text-xs text-foreground tabular-nums", day === today && "font-semibold text-primary")}>{dateOnly(day).getDate()}</span>
                {onDay.slice(0, 3).map(event => <small className={chipClass(event.marketplace)} key={event.id}>{marketplaceName(event.marketplace)}{event.discount_percent == null ? "" : ` · ${event.discount_percent}%`}</small>)}
                {onDay.length > 3 ? <small className="text-3xs text-muted-foreground">+{onDay.length - 3} more</small> : null}
              </button>;
            })}
          </div>
        </Panel>
        <div className="flex min-h-7 items-center justify-between gap-3">
          <h3 className="text-sm font-medium text-foreground">{selectedDay ? `Events on ${displayDate(selectedDay)}` : "Events this month"}</h3>
          {selectedDay ? <Button variant="ghost-muted" size="xs" onClick={() => setSelectedDay(null)}>Show month</Button> : null}
        </div>
        {visible.length === 0 ? <EmptyRow>No events here yet. Select a day and plan a sale.</EmptyRow> : null}
        <div className="grid gap-3">
          {visible.map(event => <Panel divided={false} key={event.id} className="flex flex-col gap-2 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-medium text-foreground">{event.title}</h3>
              <Badge variant={event.status === "ran" ? "success" : event.status === "planned" ? "info" : "secondary"} className={chipClass(event.marketplace, "h-auto")}>{marketplaceName(event.marketplace)} · {event.status === "ran" ? "Marked as run" : event.status === "planned" ? "Planned" : "Cancelled"}</Badge>
            </div>
            <p className="text-xs text-muted-foreground">{displayDate(event.start_date)}–{displayDate(event.end_date)} · {event.timezone}{event.discount_percent == null ? "" : ` · ${event.discount_percent}% off`}{event.items.length ? ` · ${event.items.length} items` : " · Entire marketplace"}</p>
            {event.status === "planned" && event.start_date <= today ? <PageNote className="text-warning-foreground">{event.end_date < today ? "This plan has ended. Mark it as run if you ran the sale, or cancel it." : "This sale is due. Set it up on the marketplace, then mark it as run."}</PageNote> : null}
            {event.minimum_profit != null && event.fee_percent != null && event.shipping_cost != null ? <PageNote>Estimated profit floor: {formatMoney(event.minimum_profit)} per item, using {event.fee_percent}% effective fees and {formatMoney(event.shipping_cost)} seller shipping per item. Actual profit can differ.</PageNote> : null}
            {event.marketplace === "depop" && event.status !== "cancelled" ? <PageNote>Depop reminder: apply the discount at the start and remove it at the end.</PageNote> : null}
            {event.notes ? <p className="text-sm whitespace-pre-wrap text-foreground">{event.notes}</p> : null}
            {event.items.length ? <ListingDetails summary="Included items" onOpenListing={onOpenListing}
              rows={event.items.map(item => ({ id: item.id, title: item.title, detail: `${formatMoney(item.price)} asking · ${formatMoney(item.estimated_profit)} estimated profit` }))} /> : null}
            {event.result ? <div className="flex flex-col gap-1.5 rounded-lg bg-muted-foreground/6 p-3">
              <p className="text-sm text-foreground">{event.result.ongoing ? "Results so far" : "Results"} through {displayDate(event.result.through)}: {event.result.selected.count}{event.items.length ? `/${event.items.length} selected items sold` : " marketplace sales"} · {formatMoney(event.result.selected.revenue)} revenue · {event.result.selected.profit == null ? "Profit needs complete costs and fees" : `${formatMoney(event.result.selected.profit)} recorded profit`}</p>
              <PageNote>Whole marketplace: {event.result.marketplace.count} sales · {formatMoney(event.result.marketplace.revenue)} revenue{event.result.marketplace.profit == null ? " · Profit needs complete costs and fees" : ` · ${formatMoney(event.result.marketplace.profit)} recorded profit`}.</PageNote>
              {event.result.comparison ? <PageNote>Same weekdays before the event ({displayDate(event.result.comparison_start)}–{displayDate(event.result.comparison_end)}): {event.result.comparison.count} marketplace sales · {formatMoney(event.result.comparison.revenue)} revenue{event.result.comparison.profit == null ? " · Profit unavailable" : ` · ${formatMoney(event.result.comparison.profit)} recorded profit`}.</PageNote> : <PageNote>Comparison unavailable: {event.result.comparison_unavailable}</PageNote>}
              {event.result.after ? <PageNote>In the {event.result.after_days} days after the event (up to 14): {event.result.after.count} marketplace sales · {formatMoney(event.result.after.revenue)} recorded revenue.</PageNote> : null}
              {event.result.marketplace.revenue_known < event.result.marketplace.count ? <PageNote>{event.result.marketplace.count - event.result.marketplace.revenue_known} sales have no recorded price; revenue includes recorded amounts only.</PageNote> : null}
              <PageNote>Sales during the event are associated with it; this comparison doesn’t establish that the discount caused them. Shipping uses recorded amounts.</PageNote>
            </div> : null}
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              {event.status === "planned" ? <>
                <Button size="xs" disabled={busy} onClick={() => statusMutation.mutate({ id: event.id, status: "ran" })}>I ran this sale</Button>
                <Button variant="outline" size="xs" disabled={busy} onClick={() => setForm({ start: event.start_date, end: event.end_date, marketplace: event.marketplace, event })}>Edit plan</Button>
              </> : event.fee_percent == null ? <Button variant="outline" size="xs" disabled={busy} onClick={() => setForm({ start: event.start_date, end: event.end_date, marketplace: event.marketplace, event, record: true })}>Edit record</Button> : <Button variant="outline" size="xs" disabled={busy} onClick={() => statusMutation.mutate({ id: event.id, status: "planned" })}>Return to planned</Button>}
              {event.status === "planned" ? <Button variant="ghost-muted" size="xs" disabled={busy} onClick={() => statusMutation.mutate({ id: event.id, status: "cancelled" })}>Cancel plan</Button> : null}
              <Button variant="ghost-destructive" size="xs" disabled={busy} onClick={() => void remove(event)}>Delete</Button>
            </div>
          </Panel>)}
        </div>
        {form ? <SalePlanForm key={`${form.event?.id ?? "new"}-${form.marketplace}-${form.start}`} seed={form} items={query.data.items} patterns={query.data.patterns} timezone={timezone} onOpenListing={onOpenListing}
          onClose={() => setForm(null)} onSaved={start => { setMonth(start.slice(0, 7)); setSelectedDay(null); setForm(null); setNotice(form.record ? (vendooOn ? "Sale record saved. Results will update as you resync Vendoo." : "Sale record saved. Results update as marketplace sales come in.") : "Plan saved. Set up the sale on the marketplace when you’re ready."); }} /> : null}
      </> : null}
    </PageSection>
  );
}

function SalePlanForm({ seed, items, patterns, timezone, onOpenListing, onClose, onSaved }: {
  seed: FormSeed; items: SaleCalendarItem[]; patterns: SalePattern[]; timezone: string;
  onOpenListing: (id: string) => void; onClose: () => void; onSaved: (start: string) => void;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => { formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, []);
  const event = seed.event;
  const recording = seed.record === true;
  const history = (marketplace: string) => patterns.find(pattern => pattern.marketplace === marketplace);
  const [title, setTitle] = useState(event?.title ?? `${marketplaceName(seed.marketplace)} sale`);
  const market = seed.marketplace;
  const [start, setStart] = useState(seed.start);
  const [end, setEnd] = useState(seed.end);
  const [tz, setTz] = useState(event?.timezone ?? timezone);
  const [discount, setDiscount] = useState(() => {
    if (event || recording) return event?.discount_percent == null ? "" : String(event.discount_percent);
    return String(patternDiscount(history(seed.marketplace), items) ?? "");
  });
  const [fees, setFees] = useState(String(event?.fee_percent ?? history(seed.marketplace)?.fee_percent ?? ""));
  const [shipping, setShipping] = useState(String(event?.shipping_cost ?? history(seed.marketplace)?.shipping_cost ?? 0));
  const [floor, setFloor] = useState(String(event?.minimum_profit ?? 0));
  const [notes, setNotes] = useState(event?.notes ?? "");
  const [error, setError] = useState("");
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: (command: CalendarSave) => {
      if (command.kind === "record") {
        return event ? api.saleCalendar.editRecord(event.id, command.input) : api.saleCalendar.record(command.input);
      }
      return event ? api.saleCalendar.update(event.id, command.input) : api.saleCalendar.create(command.input);
    },
    onSuccess: async (_result, command) => { await Promise.all([client.invalidateQueries({ queryKey: ["sale-calendar"] }), client.invalidateQueries({ queryKey: ["analytics"] })]); onSaved(command.input.start_date); },
  });
  const listings = items.filter(item => item.marketplaces.includes(market as SaleMarketplace));
  const costsKnown = fees.trim() !== "" && shipping.trim() !== "";
  const suggested = costsKnown ? suggestDiscount(listings, Number(fees), Number(shipping), Number(floor)) : null;
  const estimates = listings.map(item => ({ item, profit: costsKnown ? saleProfit(item.price, item.cost, Number(discount), Number(fees), Number(shipping)) : null }));
  const thin = estimates.filter(row => row.profit != null && row.profit < Number(floor));
  const uncosted = listings.filter(item => item.cost == null);

  function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (end < start) { setError("End date must be on or after the start date."); return; }
    const common = { title, marketplace: market, start_date: start, end_date: end, timezone: tz, notes };
    if (recording) {
      save.mutate({ kind: "record", input: { ...common, discount_percent: discount.trim() ? Number(discount) : null } });
    } else {
      save.mutate({ kind: "plan", input: { ...common, marketplace: market as SaleMarketplace, discount_percent: Number(discount), fee_percent: Number(fees), shipping_cost: Number(shipping), minimum_profit: Number(floor) } });
    }
  }

  return <Panel divided={false} className="border-primary/40 p-4">
    <form ref={formRef} className="flex flex-col gap-4" onSubmit={submit} aria-label={recording ? "Record marketplace sale" : event ? "Edit sale plan" : "New sale plan"}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-foreground">{recording ? "Record a sale you ran" : event ? "Edit sale plan" : "New sale plan"}</h3>
        <Button variant="ghost-muted" size="xs" disabled={save.isPending} onClick={onClose}>Close</Button>
      </div>
      <fieldset className="flex min-w-0 flex-col gap-4" disabled={save.isPending}>
        <div className="grid grid-cols-1 gap-3 @lg/page:grid-cols-2 @3xl/page:grid-cols-3">
          <FormField label="Title"><Input nativeInput autoFocus required maxLength={120} value={title} onChange={e => setTitle(e.target.value)} /></FormField>

          <FormField label="Start date"><Input nativeInput type="date" required value={start} onChange={e => setStart(e.target.value)} /></FormField>
          <FormField label="End date (inclusive)"><Input nativeInput type="date" required min={start} value={end} onChange={e => setEnd(e.target.value)} /></FormField>
          <FormField label="Event time zone"><Input nativeInput required value={tz} onChange={e => setTz(e.target.value)} /></FormField>
          <FormField label="Discount (%)"><Input nativeInput type="number" required={!recording} min={recording ? 0 : 5} max={recording ? 95 : 75} step="0.1" value={discount} onChange={e => setDiscount(e.target.value)} /></FormField>
          {!recording ? <>
            <FormField label="Estimated effective fees (%)"><Input nativeInput type="number" required min={0} max={50} step="0.1" placeholder="Include ads and processing" value={fees} onChange={e => setFees(e.target.value)} /></FormField>
            <FormField label="Seller shipping per item ($)"><Input nativeInput type="number" required min={0} max={10000} step="0.01" value={shipping} onChange={e => setShipping(e.target.value)} /></FormField>
            <FormField label="Minimum estimated profit per item ($)"><Input nativeInput type="number" required min={0} max={100000} step="0.01" value={floor} onChange={e => setFloor(e.target.value)} /></FormField>
          </> : null}
        </div>
        {!recording ? <div className="flex flex-col gap-2 rounded-lg bg-muted-foreground/6 p-3 text-sm text-foreground">
          <PageNote>The sale covers all {listings.length} active listings on {marketplaceName(market)}. Fees and shipping start from your sales over the last year; profit is the discounted price minus fees, item cost, and shipping.</PageNote>
          {!costsKnown ? <PageNote>Enter fees and shipping to get a suggested discount.</PageNote>
            : suggested != null ? <p>Suggested: <strong className="font-medium">{suggested}% off</strong>, the deepest discount up to {DISCOUNT_CEILING}% where every listing with a cost still makes at least {formatMoney(Number(floor))}.{String(suggested) !== discount ? <> <Button variant="outline" size="xs" className="ms-1" onClick={() => setDiscount(String(suggested))}>Use {suggested}%</Button></> : null}</p>
            : listings.length > uncosted.length ? <p>Even 5% off puts a listing below your {formatMoney(Number(floor))} minimum. Lower the minimum or check the listings below.</p>
            : <p>Add costs to your listings to get a suggested discount.</p>}
          {thin.length ? <ListingDetails open summary={`${thin.length} ${thin.length === 1 ? "listing makes" : "listings make"} less than ${formatMoney(Number(floor))} at ${discount || 0}% off`} onOpenListing={onOpenListing}
            rows={thin.map(({ item, profit }) => ({ id: item.id, title: item.title, detail: `${formatMoney(item.price)} asking · ${formatMoney(profit!)} estimated profit` }))} /> : null}
          {uncosted.length ? <ListingDetails summary={`${uncosted.length} ${uncosted.length === 1 ? "listing has" : "listings have"} no cost, so the suggestion can’t protect ${uncosted.length === 1 ? "it" : "them"}`} onOpenListing={onOpenListing}
            rows={uncosted.map(item => ({ id: item.id, title: item.title, detail: `${formatMoney(item.price)} asking` }))} /> : null}
        </div> : <PageNote>Records associate all sales on this marketplace during these dates with the event. Record the dates you actually ran it; a discount is optional.</PageNote>}
        <FormField label="Notes"><Textarea maxLength={2000} rows={3} value={notes} onChange={e => setNotes(e.target.value)} /></FormField>
        {error || save.isError ? <FormError>{error || save.error?.message}</FormError> : null}
        <div className="flex justify-end">
          <Button type="submit" size="sm" disabled={save.isPending}>{save.isPending ? "Saving…" : recording ? "Save record" : "Save plan"}</Button>
        </div>
      </fieldset>
    </form>
  </Panel>;
}
