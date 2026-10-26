import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLinkIcon } from "lucide-react";
import { api } from "../../api/client";
import type { EtsyListingState, EtsyMoney, EtsyOrder, EtsyOrderStatus, EtsyShop } from "../../api/etsy";
import { Stat, StatGrid } from "../AnalyticsStat";
import { EtsyNotAllowed } from "../EtsyNotAllowed";
import { EmptyRow, ListRow, PageError, PageNote, PageSection, Panel } from "../WorkspacePage";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "../ui/select";
import { Spinner } from "../ui/spinner";
import { InventoryImport } from "../InventoryImport";
import { WorkspaceTabs } from "../ebay/WorkspaceTabs";

export interface OrderPeriod { start: string; end: string; valid: boolean }
const ORDER_STATUSES: { id: EtsyOrderStatus; label: string }[] = [
  { id: "all", label: "All orders" }, { id: "paid", label: "Paid orders" }, { id: "unshipped", label: "Paid, not shipped" },
];
const LISTING_STATES: { id: EtsyListingState; label: string }[] = [
  { id: "active", label: "Active" }, { id: "draft", label: "Drafts" }, { id: "inactive", label: "Inactive" },
  { id: "sold_out", label: "Sold out" }, { id: "expired", label: "Expired" }, { id: "removed", label: "Removed" },
];

export function etsyMoney(value: EtsyMoney | null | undefined): string {
  if (!value || !value.amount.trim() || !/^[A-Z]{3}$/.test(value.currency) || !Number.isFinite(Number(value.amount))) return "Not available";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: value.currency }).format(Number(value.amount));
}

function displayDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString(undefined, { dateStyle: "medium" }) : "Date unavailable";
}

function useOrders(scope: string, period: OrderPeriod, status: EtsyOrderStatus, offset: number) {
  const params = { start: period.start, end: period.end, status, offset };
  return useQuery({ queryKey: ["etsy-workspace", "orders", scope, params], queryFn: () => api.etsy.orders(params),
    enabled: period.valid, retry: false, staleTime: 30000 });
}

function Loading({ label }: { label: string }) {
  return <div role="status" className="flex items-center gap-2 py-6 text-sm text-muted-foreground"><Spinner />{label}</div>;
}

export function EtsyDashboard({ shop, shopError, scope, period, onOpenOrders }: {
  shop?: EtsyShop; shopError: Error | null; scope: string; period: OrderPeriod; onOpenOrders: () => void;
}) {
  const orders = useOrders(scope, period, "all", 0);
  const data = period.valid ? orders.data : undefined;
  const totals = data?.summary.paidTotals;
  const gross = !data ? "Not available" : totals?.length ? totals.map(etsyMoney).join(" · ")
    : data.summary.paidOrders ? "Not available" : "No paid orders";
  return <>
    <StatGrid>
      <Stat label="Active listings" value={shop?.activeListings?.toLocaleString() ?? "Not available"} hint="Current shop total reported by Etsy" />
      <Stat label="Paid order totals" value={gross} hint="Shown orders only, before refunds and fees; includes shipping and tax" />
      <Stat label="Needs shipping" value={data ? String(data.summary.needsShipping) : "Not available"} hint="Paid physical orders on this page that are not shipped" />
    </StatGrid>
    {data ? <PageNote>Showing the newest {data.rows.length} of {data.total} orders in this period. Order totals and shipping counts cover these orders only; currencies are kept separate.
      {data.summary.missingTotals > 0 ? ` Totals are unavailable for ${data.summary.missingTotals} paid ${data.summary.missingTotals === 1 ? "order" : "orders"}.` : ""}</PageNote> : null}
    <div className="grid min-w-0 gap-6 @3xl/page:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <PageSection title="Recent orders" actions={<Button variant="outline" size="xs" onClick={onOpenOrders}>View orders</Button>}>
        {orders.error ? <PageError message={orders.error.message} /> : null}
        {period.valid && orders.isPending ? <Loading label="Loading Etsy orders…" /> : null}
        {data ? <OrderCards rows={data.rows.slice(0, 5)} /> : null}
      </PageSection>
      <PageSection title="Your shop">
        {shopError ? <PageError message={shopError.message} /> : null}
        {!shop && !shopError ? <Loading label="Loading Etsy shop…" /> : null}
        {shop ? <Panel divided={false} className="flex flex-col gap-4 p-4">
          <div><h3 className="font-semibold">{shop.name}</h3><p className="mt-1 text-sm text-muted-foreground">{shop.title}</p></div>
          <Badge variant={shop.onVacation ? "warning" : "success"} className="self-start">{shop.onVacation ? "Vacation mode" : "Shop open"}</Badge>
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div><dt className="text-xs text-muted-foreground">Items sold · all time</dt><dd className="mt-1 font-medium">{shop.soldItems?.toLocaleString() ?? "Not available"}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Shop favorites</dt><dd className="mt-1 font-medium">{shop.favorites?.toLocaleString() ?? "Not available"}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Shop rating</dt><dd className="mt-1 font-medium">{shop.reviewCount === 0 ? "No rated reviews" : shop.rating == null ? "Not available" : `${shop.rating.toFixed(1)} / 5`}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Reviews</dt><dd className="mt-1 font-medium">{shop.reviewCount?.toLocaleString() ?? "Not available"}</dd></div>
          </dl>
          {shop.url ? <a className="inline-flex items-center gap-1 text-sm underline" href={shop.url} target="_blank" rel="noreferrer">View shop on Etsy<ExternalLinkIcon className="size-3.5" /></a> : null}
        </Panel> : null}
      </PageSection>
    </div>
  </>;
}

export function EtsyOrders({ scope, period }: { scope: string; period: OrderPeriod }) {
  const [status, setStatus] = useState<EtsyOrderStatus>("all");
  return <PageSection title="Orders" actions={<a className="text-xs underline" href="https://www.etsy.com/your/orders/sold" target="_blank" rel="noreferrer">Open Etsy orders</a>}>
    <Select value={status} onValueChange={(value) => setStatus(value as EtsyOrderStatus)}>
      <SelectTrigger aria-label="Etsy order status" className="w-fit"><SelectValue>{ORDER_STATUSES.find((item) => item.id === status)?.label}</SelectValue></SelectTrigger>
      <SelectPopup>{ORDER_STATUSES.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectPopup>
    </Select>
    <OrderResults key={`${period.start}-${period.end}-${status}`} scope={scope} period={period} status={status} />
  </PageSection>;
}

export function EtsySaleAlerts({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const queryClient = useQueryClient();
  const sales = useQuery({ queryKey: ["marketplace-sales", "new"], queryFn: () => api.marketplaceSales.list(),
    refetchInterval: 15000 });
  const refresh = useMutation({
    mutationFn: () => api.marketplaceSales.reconcile("etsy"),
    retry: false,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["marketplace-sales"] }),
  });
  const rows = (sales.data?.sales ?? []).filter((sale) => sale.marketplace === "etsy");
  return <PageSection title="New Etsy sales" description="Open a matched item to review its other marketplace listings. You choose any delisting separately."
    actions={<Button variant="outline" size="xs" disabled={sales.isFetching || refresh.isPending} onClick={() => refresh.mutate()}>
      {refresh.isPending ? "Refreshing…" : "Refresh alerts"}</Button>}>
    {refresh.error ? <PageError message={refresh.error.message} /> : null}
    {refresh.data?.matched ? <PageNote>{refresh.data.matched} previously unmatched {refresh.data.matched === 1 ? "sale is" : "sales are"} now linked to inventory.</PageNote> : null}
    {sales.error ? <PageError message={sales.error.message} /> : sales.isPending ? <Loading label="Loading Etsy sale alerts…" />
      : rows.length ? <Panel><ul>{rows.map((sale) => <ListRow key={sale.id} title={sale.title || "Etsy sale"}
        meta={sale.stockStatus === "remaining" ? `${sale.remainingQuantity} remaining` : sale.stockStatus === "unknown" ? "Check remaining stock" : sale.listedElsewhere.length ? "Review other marketplaces" : "Sale received"}
        valueHint={sale.conversationId ? "Open item" : "Unmatched item"}
        onClick={sale.conversationId ? () => onOpenListing(sale.conversationId!) : undefined} />)}</ul></Panel>
        : <EmptyRow>No new Etsy sale alerts.</EmptyRow>}
  </PageSection>;
}

function OrderResults({ scope, period, status }: { scope: string; period: OrderPeriod; status: EtsyOrderStatus }) {
  const [offset, setOffset] = useState(0);
  const query = useOrders(scope, period, status, offset);
  const data = period.valid ? query.data : undefined;
  return <>
    {query.error ? <PageError message={query.error.message} /> : null}
    {period.valid && query.isPending ? <Loading label="Loading Etsy orders…" /> : null}
    {data ? <>
      <PageNote>{data.total} matching orders · {data.rows.length} shown on this page. Order amounts include shipping and tax, before refunds and fees.</PageNote>
      <OrderCards rows={data.rows} />
      <Pagination offset={offset} nextOffset={data.nextOffset} busy={query.isFetching} onChange={setOffset} />
    </> : null}
  </>;
}

function OrderCards({ rows }: { rows: EtsyOrder[] }) {
  if (!rows.length) return <EmptyRow>No orders match these dates and filters.</EmptyRow>;
  return <div className="flex flex-col gap-3">{rows.map((order) => <Panel divided={false} key={order.id} className="space-y-3 p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h3 className="text-sm font-semibold">Order #{order.id}</h3><p className="mt-1 text-xs text-muted-foreground">{displayDate(order.createdAt)} · {order.paid ? "Paid" : "Not paid"}{order.shipped ? " · Shipped" : ""}</p></div>
      <div className="flex flex-wrap items-center gap-2"><Badge variant="secondary">{order.status}</Badge>{order.needsShipping ? <Badge variant="warning">Needs shipping</Badge> : null}<span className="text-sm font-medium tabular-nums">{etsyMoney(order.total)}</span></div>
    </div>
    {order.items.length ? <ul className="space-y-1 text-sm">{order.items.map((item) => <li key={item.id} className="break-words">{item.quantity == null ? "Quantity unavailable" : `${item.quantity} ×`} {item.title}{item.sku ? <span className="text-xs text-muted-foreground"> · SKU {item.sku}</span> : null}</li>)}</ul> : null}
  </Panel>)}</div>;
}

export function EtsyListings({ scope, onOpenListing }: { scope: string; onOpenListing: (id: string) => void }) {
  const [state, setState] = useState<EtsyListingState>("active");
  const [task, setTask] = useState("Shop listings");
  return <WorkspaceTabs label="Etsy listing tools" items={["Shop listings", "Import from Etsy"]} value={task} onChange={setTask}>
    {task === "Import from Etsy" ? <InventoryImport key={scope} marketplace="etsy" accountKey={scope} onOpenListing={onOpenListing} /> : <>
    <PageSection title="Shop listings" actions={<a className="text-xs underline" href="https://www.etsy.com/your/shops/me/tools/listings" target="_blank" rel="noreferrer">Open Etsy listing manager</a>}>
      <Select value={state} onValueChange={(value) => setState(value as EtsyListingState)}>
        <SelectTrigger aria-label="Etsy listing state" className="w-fit"><SelectValue>{LISTING_STATES.find((item) => item.id === state)?.label}</SelectValue></SelectTrigger>
        <SelectPopup>{LISTING_STATES.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectPopup>
      </Select>
      <ListingResults key={state} scope={scope} state={state} />
    </PageSection>
    <EtsyNotAllowed onOpenListing={onOpenListing} />
    </>}
  </WorkspaceTabs>;
}

function ListingResults({ scope, state }: { scope: string; state: EtsyListingState }) {
  const [offset, setOffset] = useState(0);
  const query = useQuery({ queryKey: ["etsy-workspace", "listings", scope, state, offset], queryFn: () => api.etsy.listings(state, offset),
    retry: false, staleTime: 30000 });
  return <>
    {query.error ? <PageError message={query.error.message} /> : null}
    {query.isPending ? <Loading label="Loading Etsy listings…" /> : null}
    {query.data ? <>
      <PageNote>{query.data.total} listings · {query.data.rows.length} shown on this page.</PageNote>
      {!query.data.rows.length ? <EmptyRow>No listings in this state.</EmptyRow> : null}
      <div className="flex flex-col gap-3">{query.data.rows.map((listing) => <Panel divided={false} key={listing.id} className="space-y-3 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><h3 className="break-words text-sm font-semibold">{listing.title}</h3><p className="mt-1 text-xs text-muted-foreground">Listing {listing.id} · {listing.state.replace(/_/g, " ")}</p></div><span className="text-sm font-medium tabular-nums">{etsyMoney(listing.price)}</span></div>
        <p className="text-xs text-muted-foreground">Quantity: {listing.quantity ?? "Not available"} · Favorites: {listing.favorites ?? "Not available"} · Listed {displayDate(listing.createdAt)}</p>
        {listing.url ? <a className="text-xs underline" href={listing.url} target="_blank" rel="noreferrer">View on Etsy</a> : null}
      </Panel>)}</div>
      <Pagination offset={offset} nextOffset={query.data.nextOffset} busy={query.isFetching} onChange={setOffset} />
    </> : null}
  </>;
}

function Pagination({ offset, nextOffset, busy, onChange }: { offset: number; nextOffset: number | null; busy: boolean; onChange: (offset: number) => void }) {
  if (!offset && nextOffset == null) return null;
  return <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>Page {Math.floor(offset / 25) + 1}</span><div className="flex gap-2">
    <Button variant="outline" size="xs" disabled={!offset || busy} onClick={() => onChange(Math.max(0, offset - 25))}>Previous</Button>
    <Button variant="outline" size="xs" disabled={nextOffset == null || busy} onClick={() => nextOffset != null && onChange(nextOffset)}>Next</Button>
  </div></div>;
}
