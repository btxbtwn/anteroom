import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PackageSearchIcon } from "lucide-react";
import { api } from "../api/client";
import type { SourceBoxes, SourcingCart, SourcingLot, SourcingPick, SourcingPrefs, SourcingSnapshot, SourcingState } from "../api/types";
import { cn } from "../lib/utils";
import { toastManager } from "./ui/toast";
import { Badge } from "./ui/badge";
import { Button, InlineButton } from "./ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "./ui/empty";
import { Spinner } from "./ui/spinner";
import { Toggle, ToggleGroup } from "./ui/toggle-group";
import { BoughtBoxes } from "./BoughtBoxes";
import { BOXES_QUERY_KEY, boxFromLot, calibrationNote, recentlyBought, shippingNote } from "./boxPurchases";
import { DraftInput } from "./DraftInput";
import { ScoutPanel } from "./ScoutPanel";
import { formatMoney } from "./analyticsFormat";
import { PageError, PageHeader, PageNote, PageSection, PageSkeleton, Panel, WorkspacePage } from "./WorkspacePage";
import { Profit, SourcingCheck, SourcingDetails, SourcingItem, SourcingList, SourcingSetting, sourcingReasonClassName, sourcingTitleClassName } from "./sourcingUi";
import {
  REFRESH_HOURS,
  boxCount,
  cartTotal,
  clockTime,
  exclusionSummary,
  freeShippingGap,
  isZip,
  lotReason,
  nextUpdate,
  otherLots,
  pickNote,
  planMetrics,
  planNeedsUpdate,
  saleDate,
  storeName,
  whyNotPicked,
} from "./boxScout";

const QUERY_KEY = ["sourcing"];

interface Props {
  onOpenListing: (convId: string) => void;
}

const TAB_KEY = "studio.sourcing.tab";

export function SourcingPage({ onOpenListing }: Props) {
  const [tab, setTab] = useState(() => localStorage.getItem(TAB_KEY) === "scout" ? "scout" : "boxes");
  const choose = (next: "boxes" | "scout") => {
    localStorage.setItem(TAB_KEY, next);
    setTab(next);
  };
  return (
    <WorkspacePage width="wide">
      <ToggleGroup
        aria-label="Sourcing"
        value={[tab]}
        onValueChange={(next) => {
          const value = next[0];
          if (value === "boxes" || value === "scout") choose(value);
        }}
      >
        <Toggle value="boxes">Wholesale boxes</Toggle>
        <Toggle value="scout">Scout an item</Toggle>
      </ToggleGroup>
      {tab === "scout" ? (
        <>
          <PageHeader
            title="Is it worth buying?"
            description={<>
              Photograph an item and its tag in the store. Anteroom identifies it, looks up what it has sold for,
              and tells you the most worth paying. Nothing is bought or listed.
            </>}
          />
          <ScoutPanel onOpenListing={onOpenListing} />
        </>
      ) : (
        <BoxSourcing onOpenListing={onOpenListing} />
      )}
    </WorkspacePage>
  );
}

function BoxSourcing({ onOpenListing }: Props) {
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState("all");
  const query = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.sourcing.get,
    // Keep watching until the list catches up with saved shipping and budget settings.
    refetchInterval: (current) => {
      const state = current.state.data;
      return state && (state.refreshing || planNeedsUpdate(state)) ? 3000 : false;
    },
  });
  const onSaved = (state: SourcingState) => queryClient.setQueryData(QUERY_KEY, state);
  const onError = (error: Error) =>
    toastManager.add({ type: "error", title: "Could not update your buy list", description: error.message });
  const refresh = useMutation({ mutationFn: api.sourcing.refresh, onSuccess: onSaved, onError });
  const savePrefs = useMutation({
    mutationFn: (prefs: Partial<SourcingPrefs>) => api.sourcing.savePrefs(prefs),
    onSuccess: onSaved,
    onError,
  });
  const data = query.data;
  const snapshot = data?.snapshot ?? null;
  const selectedSnapshot = snapshot ? {
    ...snapshot,
    buy_list: choice === "all" ? snapshot.buy_list : snapshot.store_buy_lists[choice],
    store_picks: choice === "all" ? snapshot.store_picks : { [choice]: snapshot.store_picks[choice] ?? [] },
    lots: snapshot.lots.filter((lot) => choice === "all" || lot.store === choice),
  } : null;
  const updating = !!data && (data.refreshing || refresh.isPending || savePrefs.isPending
    || planNeedsUpdate(data));

  return (
    <>
      <PageHeader
        title="Source with sales evidence"
        description={<>
          Compare wholesale boxes using recent comparable sales, current competition, and your costs.
          A mixed box’s contents and future sales remain uncertain.
        </>}
        actions={data ? (
          <UpdateStatus
            snapshot={snapshot}
            refreshing={data.refreshing || refresh.isPending}
            onRefresh={() => refresh.mutate()}
          />
        ) : null}
      >
        {data ? (
          <Settings prefs={data.prefs} measured={data.snapshot?.assumptions ?? null} saving={savePrefs.isPending} onSave={(prefs) => savePrefs.mutate(prefs)} />
        ) : null}
      </PageHeader>

      {query.isLoading ? <PageSkeleton label="Loading your buy list…" cards={3} /> : null}
      {query.isError ? <PageError message={(query.error as Error).message || "Could not load your buy list."} /> : null}

      {data && !snapshot ? (
        <Panel divided={false}>
          <Empty size="compact">
            <EmptyHeader>
              <EmptyMedia variant="icon"><Spinner aria-hidden role={undefined} /></EmptyMedia>
              <EmptyTitle>Getting your first list ready…</EmptyTitle>
              <EmptyDescription>Anteroom is reading the suppliers and collecting dated sold listings. Only boxes with enough recent evidence can qualify. This takes a few minutes.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        </Panel>
      ) : null}
      {data && !snapshot ? <BoughtBoxes /> : null}

      {data && snapshot ? (
        <>
          <StoreProblems snapshot={snapshot} />
          <SeasonalContext snapshot={snapshot} />
          <ResearchCoverage snapshot={snapshot} ebayConnected={data.ebay_connected} />
          <Choices snapshot={snapshot} choice={choice} onChoose={setChoice} />
          {updating ? (
            <p className="inline-flex items-center gap-2 text-xs text-muted-foreground" role="status">
              <Spinner size="xs" aria-hidden role={undefined} />
              Updating your options. Cart links will be ready when the check finishes.
            </p>
          ) : null}
          {selectedSnapshot ? <>
            <BuyList snapshot={selectedSnapshot} choice={choice} updating={updating} onOpenListing={onOpenListing} />
            <SupplierPicks snapshot={selectedSnapshot} onOpenListing={onOpenListing} />
            <AvailableBoxes snapshot={selectedSnapshot} onOpenListing={onOpenListing} />
          </> : null}
          <BoughtBoxes />
          <Trending trend={data.trend} />
          <HowItWorks snapshot={snapshot} />
        </>
      ) : null}
    </>
  );
}

function UpdateStatus({
  snapshot,
  refreshing,
  onRefresh,
}: {
  snapshot: SourcingSnapshot | null;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  if (refreshing) {
    return (
      <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
        <Spinner size="xs" aria-hidden role={undefined} />
        Updating…
      </span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
      {snapshot ? `Updated ${clockTime(snapshot.updated_at)} · next ${clockTime(nextUpdate(snapshot.updated_at))}` : ""}
      <Button variant="outline" size="xs" onClick={onRefresh}>Update now</Button>
    </span>
  );
}

function Settings({
  prefs,
  measured,
  saving,
  onSave,
}: {
  prefs: SourcingPrefs;
  measured: SourcingSnapshot["assumptions"] | null;
  saving: boolean;
  onSave: (prefs: Partial<SourcingPrefs>) => void;
}) {
  const otherZips = prefs.recent_zips.filter((zip) => zip !== prefs.zip);
  const weeks = (count: number) => `${count} ${count === 1 ? "week" : "weeks"}`;
  return (
    <Panel divided={false} className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <SourcingSetting label="Budget">
          <DraftInput
            value={String(prefs.budget)}
            label="Most to spend on boxes and inbound shipping before tax"
            prefix="$"
            width="5.5rem"
            inputMode="decimal"
            disabled={saving}
            clean={(text) => text.replace(/[^\d.]/g, "")}
            accept={(text) => Number.isFinite(Number(text)) && Number(text) > 0}
            onCommit={(text) => onSave({ budget: Number(text) })}
          />
        </SourcingSetting>
        <SourcingSetting label="Ship to">
          <DraftInput
            value={prefs.zip}
            label="ZIP code the boxes ship to"
            width="5.5rem"
            inputMode="numeric"
            maxLength={5}
            disabled={saving}
            clean={(text) => text.replace(/\D/g, "")}
            accept={isZip}
            onCommit={(zip) => onSave({ zip })}
          />
          {otherZips.length ? <span>or</span> : null}
          {otherZips.map((zip) => (
            <Badge
              key={zip}
              variant="outline"
              size="control"
              render={<button type="button" disabled={saving} title={`Ship to ${zip} instead`} onClick={() => onSave({ zip })} />}
            >
              {zip}
            </Badge>
          ))}
        </SourcingSetting>
        <SourcingSetting label="Minimum return">
          <DraftInput
            value={String(Math.round(prefs.min_roi * 100))}
            label="Minimum estimated profit as a percentage of purchase cost"
            suffix="%"
            width="5.5rem"
            inputMode="decimal"
            disabled={saving}
            clean={(text) => text.replace(/[^\d.]/g, "")}
            accept={(text) => text !== "" && Number.isFinite(Number(text)) && Number(text) >= 0}
            onCommit={(text) => onSave({ min_roi: Number(text) / 100 })}
          />
        </SourcingSetting>
      </div>
      <SourcingDetails summary={<>More options: selling in {weeks(prefs.ready_in_weeks)} for {weeks(prefs.selling_window_weeks)} · {Math.round(prefs.sell_through * 100)}% sells · {Math.round(prefs.fees * 100)}% fees · {formatMoney(prefs.cost_per_piece)} per piece{prefs.raghouse_vip ? " · Raghouse VIP" : ""}</>}>
        <div className="grid gap-3 pt-1 @2xl/page:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <SourcingSetting label="Start selling in">
              <DraftInput value={String(prefs.ready_in_weeks)} label="Weeks for shipping, preparation and listing" suffix="weeks" width="5rem" inputMode="numeric" disabled={saving}
                clean={(text) => text.replace(/\D/g, "")} accept={(text) => text !== "" && Number(text) <= 26}
                onCommit={(text) => onSave({ ready_in_weeks: Number(text) })} />
            </SourcingSetting>
          </div>
          <div className="flex flex-col gap-1.5">
            <SourcingSetting label="Sell for">
              <DraftInput value={String(prefs.selling_window_weeks)} label="Length of the target selling window in weeks" suffix="weeks" width="5rem" inputMode="numeric" disabled={saving}
                clean={(text) => text.replace(/\D/g, "")} accept={(text) => Number(text) >= 1 && Number(text) <= 26}
                onCommit={(text) => onSave({ selling_window_weeks: Number(text) })} />
            </SourcingSetting>
            <SourcingCheck checked={prefs.selling_window_auto} disabled={saving || !measured?.selling_window_measured}
              onChange={(checked) => onSave({ selling_window_auto: checked })}>
              {measured?.selling_window_measured
                ? `From how long your last ${measured.selling_window_measured.sales} sales took`
                : "From your sales once you have 30 in a year"}
            </SourcingCheck>
          </div>
          <div className="flex flex-col gap-1.5">
            <SourcingSetting label="Pieces that sell">
              <DraftInput value={String(Math.round(prefs.sell_through * 100))} label="Percentage of usable pieces you plan to sell" suffix="%" width="5rem" inputMode="decimal" disabled={saving}
                clean={(text) => text.replace(/[^\d.]/g, "")} accept={(text) => Number(text) > 0 && Number(text) <= 100}
                onCommit={(text) => onSave({ sell_through: Number(text) / 100 })} />
            </SourcingSetting>
            <SourcingCheck checked={prefs.sell_through_auto} disabled={saving || !measured?.sell_through_measured}
              onChange={(checked) => onSave({ sell_through_auto: checked })}>
              {measured?.sell_through_measured
                ? `From your Analytics: ${measured.sell_through_measured.sales} sales in 12 months`
                : "From your Analytics once you have 30 sales in a year"}
            </SourcingCheck>
          </div>
          <div className="flex flex-col gap-1.5">
            <SourcingSetting label="Selling fees">
              <DraftInput value={String(Math.round(prefs.fees * 100))} label="Effective marketplace and payment fees as a percentage of item revenue" suffix="%" width="5rem" inputMode="decimal" disabled={saving}
                clean={(text) => text.replace(/[^\d.]/g, "")} accept={(text) => text !== "" && Number(text) >= 0 && Number(text) < 100}
                onCommit={(text) => onSave({ fees: Number(text) / 100 })} />
            </SourcingSetting>
            <SourcingCheck checked={prefs.fees_auto} disabled={saving || !measured?.fees_measured}
              onChange={(checked) => onSave({ fees_auto: checked })}>
              {measured?.fees_measured
                ? `From your ${measured.fees_measured.sales} recent sales`
                : "From your sales once you have 30 in a year"}
            </SourcingCheck>
          </div>
          <div className="flex flex-col gap-1.5">
            <SourcingSetting label="Cost per sold piece">
              <DraftInput value={String(prefs.cost_per_piece)} label="Operating allowance per usable piece" prefix="$" width="5rem" inputMode="decimal" disabled={saving}
                clean={(text) => text.replace(/[^\d.]/g, "")} accept={(text) => text !== "" && Number.isFinite(Number(text)) && Number(text) >= 0}
                onCommit={(text) => onSave({ cost_per_piece: Number(text) })} />
            </SourcingSetting>
            <SourcingCheck checked={prefs.cost_per_piece_auto} disabled={saving || !measured?.cost_per_piece_measured}
              onChange={(checked) => onSave({ cost_per_piece_auto: checked })}>
              {measured?.cost_per_piece_measured
                ? `From your ${measured.cost_per_piece_measured.sales} recent sales, at least ${formatMoney(2)}`
                : "From your sales once you have 30 in a year"}
            </SourcingCheck>
          </div>
          <div className="flex flex-col gap-1.5">
            <SourcingCheck checked={prefs.raghouse_vip} disabled={saving} onChange={(checked) => onSave({ raghouse_vip: checked })}>
              Raghouse VIP member
            </SourcingCheck>
          </div>
        </div>
        <PageNote>Cost per piece is what you spend to prepare, pack and ship each piece that sells. It is charged on the pieces planned to sell, in the plan and in the lower-sales test. Purchase tax is extra.</PageNote>
      </SourcingDetails>
    </Panel>
  );
}

function SeasonalContext({ snapshot }: { snapshot: SourcingSnapshot }) {
  const { window, seller_history: history } = snapshot.seasonality;
  const date = (value: string) => new Date(`${value}T12:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  return <Panel divided={false} className="flex flex-col gap-2 p-4" aria-label="Seasonal sourcing context">
    <strong className="text-sm font-medium text-foreground">Buying now for {date(window.start_date)} – {date(window.end_date)}</strong>
    <PageNote>Research targets this selling window. Matching themes get priority among qualifying boxes; seasonality does not raise estimated prices or sales.</PageNote>
    {history.groups.length ? <SourcingDetails summary={`Your sales in the same calendar windows: ${history.groups.length} category ${history.groups.length === 1 ? "group" : "groups"}`}>
      <PageNote>Looking back up to three years. These are recorded sales, not a measure of demand or your chance of selling a box. Prices below are historical; box estimates use recent sold evidence.</PageNote>
      {history.groups.map((group) => <PageNote key={`${group.category_path}:${group.item_type}`}>
        {group.category_path}{group.item_type ? ` · ${group.item_type}` : ""}: {group.count} sales · historical median {formatMoney(group.historical_median_price)}
      </PageNote>)}
    </SourcingDetails> : <PageNote>{history.matching_window_sales} recorded sales in the same calendar windows over the previous three years. No category/type group has the {history.minimum_group_sales} sales needed for personal context yet. Anteroom can still research seasonal themes.</PageNote>}
  </Panel>;
}

function Choices({ snapshot, choice, onChoose }: {
  snapshot: SourcingSnapshot;
  choice: string;
  onChoose: (choice: string) => void;
}) {
  const hasRecommendations = [snapshot.buy_list, ...Object.values(snapshot.store_buy_lists)].some((plan) => plan.carts.length > 0);
  return (
    <PageSection
      aria-label="Compare sourcing options"
      title={hasRecommendations ? "Choose a buy list" : "Browse by supplier"}
      description={hasRecommendations
        ? `Each option uses the same ${formatMoney(snapshot.buy_list.budget)} budget including estimated shipping. Choose one; these are alternatives. Tax is extra.`
        : "Choose all boxes or a supplier to browse what Anteroom checked. Recommendations appear when the evidence and cost checks pass."}
    >
      {Object.entries(snapshot.calibration ?? {}).map(([store, calibration]) => {
        const note = calibrationNote(storeName(store), calibration);
        return note ? <PageNote key={store}>{note}</PageNote> : null;
      })}
      {snapshot.assumptions.market_haircut && snapshot.assumptions.market_haircut.factor < 1 ? <PageNote>
        Marketplace prices are scaled to {Math.round(snapshot.assumptions.market_haircut.factor * 100)}% because your own sales ran lower than the listings for {snapshot.assumptions.market_haircut.themes} kinds of piece. Prices from your own sales are not scaled.
      </PageNote> : null}
      {snapshot.vip_upside && snapshot.vip_upside.boxes > 0 ? <PageNote>
        Raghouse VIP ({formatMoney(snapshot.vip_upside.monthly_fee)}/month) would add {boxCount(snapshot.vip_upside.boxes)} to this list,
        about {formatMoney(Math.round(snapshot.vip_upside.extra_profit))} more estimated profit. Turn on “Raghouse VIP member” under More options once you join.
      </PageNote> : null}
      <div className="grid gap-3 @lg/page:grid-cols-2 @3xl/page:grid-cols-4">
        {["all", ...Object.keys(snapshot.stores)].map((key) => {
          const plan = key === "all" ? snapshot.buy_list : snapshot.store_buy_lists[key];
          const count = plan.carts.reduce((sum, cart) => sum + cart.lots.length, 0);
          const metrics = planMetrics(plan);
          const pressed = choice === key;
          return (
            <button
              key={key}
              type="button"
              aria-pressed={pressed}
              onClick={() => onChoose(key)}
              className={cn(
                "flex cursor-pointer flex-col gap-1 rounded-xl border bg-card/40 p-3.5 text-left text-xs text-muted-foreground shadow-xs/5 outline-none transition-colors hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring",
                pressed ? "border-primary/60 bg-primary/6" : "border-border/60",
              )}
            >
              <strong className="text-sm font-medium text-foreground">{key === "all" ? (hasRecommendations ? "Combined list" : "All suppliers") : storeName(key)}</strong>
              {count ? <>
                <span>{boxCount(count)} · {formatMoney(plan.total)}</span>
                <span>Est. profit {formatMoney(Math.round(plan.expected_profit))}</span>
                <span>Lower-sales test {formatMoney(Math.round(metrics.downside))}</span>
              </> : <>
                <span>{snapshot.stores[key]?.error ? "Store unavailable" : boxCount(snapshot.lots.filter((lot) => key === "all" || lot.store === key).length)}</span>
                <span>No recommendations yet</span>
              </>}
            </button>
          );
        })}
      </div>
    </PageSection>
  );
}

function BuyList({
  snapshot,
  choice,
  updating,
  onOpenListing,
}: {
  snapshot: SourcingSnapshot;
  choice: string;
  updating: boolean;
  onOpenListing: (convId: string) => void;
}) {
  const plan = snapshot.buy_list;
  const hasEvidence = snapshot.lots.some((lot) => lot.resale_per_pc != null);
  if (plan.carts.length === 0) {
    if (choice !== "all" && snapshot.stores[choice]?.error) {
      return <Panel divided={false}>
        <Empty size="compact">
          <EmptyHeader>
            <EmptyMedia variant="icon"><PackageSearchIcon /></EmptyMedia>
            <EmptyTitle>{storeName(choice)} is unavailable</EmptyTitle>
            <EmptyDescription>Anteroom couldn’t check this store. Try updating again, or compare the other buy lists.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </Panel>;
    }
    return (
      <Panel divided={false}>
        <Empty size="compact">
          <EmptyHeader className="max-w-xl">
            <EmptyMedia variant="icon"><PackageSearchIcon /></EmptyMedia>
            <EmptyTitle>{!snapshot.lots.length ? "No boxes available in this check"
              : hasEvidence ? "No recommendations within these settings" : "No recommendations yet — sold evidence is missing"}</EmptyTitle>
            <EmptyDescription>{snapshot.lots.length ? (hasEvidence
              ? `No box passes all the evidence, return and lower-sales checks within ${formatMoney(plan.budget)} including shipping. Of ${boxCount(snapshot.lots.length)}: ${exclusionSummary(plan)}. Browse the boxes below to see what ruled each one out, or change the settings above.`
              : `Anteroom found ${boxCount(snapshot.lots.length)}, but has no usable recent sold evidence to estimate their resale. Each theme needs at least 3 distinct comparable sales within 30 days. The boxes are shown below for you to review.`)
              : "This check returned no available boxes for the selected suppliers. Review any supplier errors above."}</EmptyDescription>
            <EmptyDescription className="text-xs">Next automatic check around {clockTime(nextUpdate(snapshot.updated_at))}.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </Panel>
    );
  }
  const boxes = plan.carts.reduce((sum, cart) => sum + cart.lots.length, 0);
  const metrics = planMetrics(plan);
  return (
    <>
      <section className="flex flex-col gap-3 rounded-xl border border-primary/24 bg-primary/6 p-4" aria-label="Summary">
        <div className="text-lg font-semibold tracking-tight text-foreground">
          {boxCount(boxes)} to review · {formatMoney(Math.round(plan.total))} with shipping
        </div>
        <div className="text-sm text-muted-foreground">
          Estimated profit <strong className="font-semibold text-success-foreground">{formatMoney(Math.round(plan.expected_profit))}</strong> after fees and {formatMoney(metrics.operating)} in operating costs, before tax.
        </div>
        <dl className="grid grid-cols-1 gap-3 @lg/page:grid-cols-3">
          {([
            ["Usable pieces (estimate)", String(Math.round(metrics.usable))],
            ["Sales in your plan", `~${Math.round(metrics.sold)}`],
            ["Lower-sales test profit", formatMoney(Math.round(metrics.downside))],
          ] as const).map(([label, value]) => (
            <div key={label} className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-base font-medium text-foreground tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <PageNote>{formatMoney(Math.max(0, plan.budget - plan.total))} of your purchase budget remains before tax. Keep another {formatMoney(metrics.operating)} for operating costs. The lower-sales test assumes only half your planned sales, at each theme’s lowest retained sale price, with operating costs on those pieces. It is a sensitivity test, not a guaranteed minimum profit.</PageNote>
      {plan.carts.map((cart, index) => (
        <Cart key={cart.store} cart={cart} updating={updating} step={plan.carts.length > 1 ? index + 1 : null} of={plan.carts.length} onOpenListing={onOpenListing} />
      ))}
    </>
  );
}

function Cart({ cart, step, of, updating, onOpenListing }: {
  cart: SourcingCart; step: number | null; of: number; updating: boolean; onOpenListing: (convId: string) => void;
}) {
  const gap = freeShippingGap(cart);
  return (
    <section className="flex flex-col gap-3" aria-label={cart.name}>
      <div className="flex flex-wrap items-center gap-2">
        {step ? <Badge variant="info">Step {step} of {of}</Badge> : null}
        <h2 className="text-sm font-medium text-foreground">{cart.name} · {boxCount(cart.lots.length)}</h2>
      </div>
      <Panel>
        <ol className="divide-y divide-border/50">
          {cart.lots.map((lot) => (
            <Item key={`${lot.store}:${lot.variant_id}`} lot={lot} storeName={cart.name} onOpenListing={onOpenListing} />
          ))}
        </ol>
        <div className="flex flex-col gap-3 px-4 py-3">
          <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Boxes</dt>
            <dd className="text-right text-foreground tabular-nums">{formatMoney(cart.subtotal)}</dd>
            <dt className="text-muted-foreground">Shipping{cart.free_shipping ? "" : " (estimate)"}</dt>
            <dd className="text-right text-foreground tabular-nums">{cart.free_shipping ? "Free" : formatMoney(Math.round(cart.shipping))}</dd>
            <dt className="font-medium text-foreground">Total</dt>
            <dd className="text-right font-medium text-foreground tabular-nums">{formatMoney(Math.round(cartTotal(cart)))}</dd>
          </dl>
          {gap ? (
            <PageNote>Free shipping starts at {formatMoney(cart.free_shipping_over!)} in boxes ({formatMoney(gap)} more).</PageNote>
          ) : null}
          <div className="flex flex-col gap-2 @xl/page:flex-row @xl/page:items-center">
            {updating ? (
              <Button disabled>
                <Spinner size="sm" aria-hidden role={undefined} />
                Updating cart…
              </Button>
            ) : (
              <Button render={<a href={cart.cart_url} target="_blank" rel="noopener noreferrer" />}>
                {cart.cart_fills ? `Open ${cart.name} cart` : `Open on ${cart.name}`}
              </Button>
            )}
            <PageNote>{cart.cart_fills
              ? "The cart opens with these boxes already in it. Check it, then pay."
              : `Each ${cart.name} lot is bought from its own page after signing in; shipping and card processing are itemised at checkout. Open each box above.`}</PageNote>
          </div>
        </div>
      </Panel>
    </section>
  );
}

function Item({ lot, storeName, note, onOpenListing }: {
  lot: SourcingLot; storeName: string; note?: string; onOpenListing: (convId: string) => void;
}) {
  const queryClient = useQueryClient();
  const boxes = useQuery({ queryKey: BOXES_QUERY_KEY, queryFn: api.boxes.list });
  const record = useMutation({
    mutationFn: () => api.boxes.create(boxFromLot(lot)),
    onSuccess: (data: SourceBoxes) => {
      queryClient.setQueryData(BOXES_QUERY_KEY, data);
      toastManager.add({
        type: "success",
        title: "Added to your boxes",
        description: `Correct the shipping under Boxes you bought once ${storeName} charges it.`,
      });
    },
    onError: (error: Error) =>
      toastManager.add({ type: "error", title: "Could not record the box", description: error.message }),
  });
  const bought = recentlyBought(lot, boxes.data?.boxes ?? []);
  return (
    <SourcingItem
      main={<>
        <a className={sourcingTitleClassName} href={lot.url} target="_blank" rel="noopener noreferrer">
          {lot.title}
        </a>
        <div className={sourcingReasonClassName}>
          {lotReason(lot)}
          {lot.trend_hits.length ? <span className="text-info-foreground"> · selling-window match: {lot.trend_hits.join(", ")}</span> : null}
        </div>
        <div className={sourcingReasonClassName}>About {formatMoney(lot.cog_per_usable_pc)} per usable piece, including shipping</div>
        {lot.break_even_pcs != null ? <div className={sourcingReasonClassName}>
          Break even after {lot.break_even_pcs} sales · plan assumes ~{Math.round(lot.usable_pcs * lot.sell_through)} sales
        </div> : null}
        {lot.pcs_estimated || lot.lbs_estimated ? <div className="text-xs text-warning-foreground">{lot.pcs_estimated ? "Piece count is estimated from weight. " : ""}{lot.lbs_estimated ? "Shipping weight is estimated. " : ""}Check the supplier’s lot details.</div> : null}
        <ResearchEvidence lot={lot} onOpenListing={onOpenListing} />
      </>}
      money={<>
        <div>{formatMoney(Math.round(lot.landed))}</div>
        {lot.expected_profit != null ? <Profit value={lot.expected_profit} suffix=" profit" /> : null}
        {lot.downside_profit != null ? <div className="text-xs text-muted-foreground">Lower-sales test {formatMoney(Math.round(lot.downside_profit))}</div> : null}
        {note ? <div className="text-xs text-muted-foreground">{note}</div> : null}
        <Button
          variant="outline"
          size="xs"
          className="mt-1"
          disabled={bought || record.isPending || !boxes.data}
          onClick={() => record.mutate()}
        >
          {bought ? "Bought" : "I bought this"}
        </Button>
      </>}
    />
  );
}

function SupplierPicks({ snapshot, onOpenListing }: { snapshot: SourcingSnapshot; onOpenListing: (convId: string) => void }) {
  const stores = Object.entries(snapshot.store_picks).filter(([, picks]) => picks.length > 0);
  if (stores.length === 0) return null;
  return (
    <>
      {stores.map(([store, picks]: [string, SourcingPick[]]) => (
        <PageSection
          key={store}
          aria-label={`Top boxes at ${storeName(store)}`}
          title={`Top ${picks.length} at ${storeName(store)}`}
          description="The supplier’s best boxes, each judged as if bought on its own: those that pass every check first, then the closest misses. They don’t share the budget the way a buy list does."
        >
          <SourcingList>
            {picks.map((pick) => (
              <Item key={`${pick.store}:${pick.variant_id}`} lot={pick} storeName={storeName(store)} note={pickNote(pick)} onOpenListing={onOpenListing} />
            ))}
          </SourcingList>
        </PageSection>
      ))}
    </>
  );
}

function AvailableBoxes({ snapshot, onOpenListing }: { snapshot: SourcingSnapshot; onOpenListing: (convId: string) => void }) {
  const lots = otherLots(snapshot);
  if (lots.length === 0) return null;
  return (
    <PageSection
      aria-label="Available boxes"
      title={`${snapshot.buy_list.carts.length ? "Other available boxes" : "Available boxes"} · ${boxCount(lots.length)}`}
      description="Every other box the suppliers have in stock, best first. Each row shows why it isn’t in the recommended buy list; you can still open the supplier’s listing."
    >
      <SourcingList>
        {lots.map((lot) => (
          <SourcingItem
            key={`${lot.store}:${lot.variant_id}`}
            main={<>
              <a className={sourcingTitleClassName} href={lot.url} target="_blank" rel="noopener noreferrer">
                {lot.title}
              </a>
              <div className={sourcingReasonClassName}>{lotReason(lot, true)}</div>
              <ResearchEvidence lot={lot} onOpenListing={onOpenListing} />
            </>}
            money={<>
              <div>{formatMoney(Math.round(lot.landed))}</div>
              <div className="max-w-56 text-xs text-muted-foreground">{whyNotPicked(lot, snapshot.buy_list)}</div>
            </>}
          />
        ))}
      </SourcingList>
    </PageSection>
  );
}

const ALL_SALES_SOURCE = "Your sales (all items)";

function ResearchEvidence({ lot, onOpenListing }: { lot: SourcingLot; onOpenListing: (convId: string) => void }) {
  const [open, setOpen] = useState(false);
  // The examples are fetched when the seller opens them, so the page itself stays light.
  const evidence = useQuery({
    queryKey: ["sourcing", "evidence", lot.theme],
    queryFn: () => api.sourcing.evidence(lot.theme),
    enabled: open,
    staleTime: 5 * 60_000,
  });
  if (!lot.comps_count) return <div className="text-xs text-muted-foreground">Not enough recent sold evidence to estimate resale.</div>;
  const data = evidence.data;
  // A mixed box is priced from everything the seller sold, not a search for one kind of piece.
  const allItems = lot.research_source === ALL_SALES_SOURCE;
  const compRow = "flex flex-col gap-0.5 py-1.5";
  return <SourcingDetails
    className="mt-1"
    open={open}
    onToggle={setOpen}
    summary={<>{allItems ? "Your recent sales of any kind" : `${lot.comps_count} sales · ${lot.research_source}`} · {lot.research_at ? clockTime(lot.research_at) : ""}</>}
  >
    {evidence.isLoading ? <span className="inline-flex items-center gap-2 text-muted-foreground" role="status"><Spinner size="xs" aria-hidden role={undefined} />Loading the sales…</span> : null}
    {evidence.isError ? <PageNote className="text-destructive-foreground">{(evidence.error as Error).message}</PageNote> : null}
    {data ? <>
      <PageNote>{allItems
        ? `A mixed box can't be priced by searching for one kind of piece, so this uses the median of everything you sold in the last 90 days. These are your ${data.comps.length} most recent sales.`
        : data.source === "Your sales"
        ? "These are your own recorded sales of this kind of piece."
        : "Anteroom read these sold listings from the marketplaces by searching for this kind of piece, leaving out any whose title shows a different garment, a bundle or new with tags. It cannot check brand, era or condition. Review the links before buying."}</PageNote>
      <ul className="divide-y divide-border/50">
        {data.comps.map((comp) => <li key={comp.conversation_id ?? comp.url} className={compRow}>
          {comp.conversation_id
            ? <InlineButton className="justify-start text-left whitespace-normal" onClick={() => onOpenListing(comp.conversation_id!)}>{comp.title}</InlineButton>
            : <a className="text-foreground hover:underline" href={comp.url} target="_blank" rel="noopener noreferrer">{comp.title}</a>}
          <span className="text-muted-foreground">{comp.conversation_id ? "Your sale" : comp.marketplace} · {formatMoney(comp.price)} · sold {saleDate(comp.sold_at)}</span>
          {comp.conversation_id ? null : <q className="text-muted-foreground italic">{comp.snippet}</q>}
        </li>)}
      </ul>
      {allItems ? null : data.active_median != null ? <PageNote>Current asking-price median {formatMoney(data.active_median)}; the resale estimate is capped at this price.</PageNote>
        : <PageNote>Current competition sample is too small to cap the price. Market sell-through and time to sell are unknown.</PageNote>}
      {data.active.length ? <ul className="divide-y divide-border/50">
        {data.active.map((comp) => <li key={comp.url} className={compRow}>
          <a className="text-foreground hover:underline" href={comp.url} target="_blank" rel="noopener noreferrer">{comp.title}</a>
          <span className="text-muted-foreground">{comp.marketplace} · asking {formatMoney(comp.price)}</span>
        </li>)}
      </ul> : null}
    </> : null}
  </SourcingDetails>;
}

function ResearchCoverage({ snapshot, ebayConnected }: { snapshot: SourcingSnapshot; ebayConnected: boolean }) {
  const priced = snapshot.lots.filter((lot) => lot.resale_per_pc != null).length;
  return <div className="flex flex-col gap-1 text-xs text-muted-foreground" role="status">
    <strong className="text-sm font-medium text-foreground">{boxCount(snapshot.lots.length)} in this check · {priced} with enough recent sold evidence.</strong>
    <span>At least 3 distinct sales within 30 days: your own sales first, then eBay sold listings, then Poshmark. Prices are checked weekly. Sales evidence supports an estimate; it does not measure the chance your box will sell.</span>
    {ebayConnected ? null : <span className="text-warning-foreground">Chrome isn’t connected, so eBay sold listings can’t be read. Connect Chrome and update to add them.</span>}
  </div>;
}

function Trending({ trend }: { trend: SourcingState["trend"] }) {
  if (trend.terms.length === 0) return null;
  return (
    <PageSection
      aria-label="Selling-window research"
      title="Themes to research"
      description={`Seasonal reports and your selling window guide research and prioritize qualifying boxes. A theme match cannot qualify a box or increase its projected sales. ${trend.updated_at ? `Researched ${clockTime(trend.updated_at)}.` : ""}`}
    >
      <div className="flex flex-wrap gap-1.5">
        {trend.terms.map((term) => <Badge key={term} variant="outline" size="lg" className="font-normal">{term}</Badge>)}
      </div>
    </PageSection>
  );
}

function StoreProblems({ snapshot }: { snapshot: SourcingSnapshot }) {
  const problems = Object.values(snapshot.stores).filter((store) => store.error);
  if (problems.length === 0) return null;
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-warning/32 bg-warning-surface px-4 py-3 text-sm text-warning-foreground" role="status">
      {problems.map((store) => (
        <p key={store.name}>
          {store.error} Its boxes are left out until the next update.
        </p>
      ))}
    </div>
  );
}

function HowItWorks({ snapshot }: { snapshot: SourcingSnapshot }) {
  const a = snapshot.assumptions;
  return (
    <SourcingDetails summary="How Anteroom picks boxes">
      <ul className="flex list-disc flex-col gap-2 ps-5 leading-relaxed text-muted-foreground">
        <li>Every {REFRESH_HOURS} hours it reads every box Raghouse, Thrift Vintage Fashion and PaperCrane list. Raghouse Recycle boxes, Thrift Vintage Fashion A/B, B, B/C and C grades and PaperCrane Mixed/As-Is (C) lots are never considered; PaperCrane Cream (A) and Standard (B) lots are covered by its Buyer Protection beyond a 5% or 10% tolerance for unsellable pieces.</li>
        <li>
          It groups boxes by the kind of piece inside (“vintage graphic t-shirts”, “hawaiian shirts”) and prices each kind from your own recorded sales of that kind,
          then eBay sold listings read through your Chrome, then Poshmark’s, rechecked weekly. A mixed box is priced from all your recent sales. It requires dated sold examples and computes their median, capped by comparable asking prices when enough are available.
        </li>
        <li>
          Profit uses your {Math.round(a.sell_through * 100)}% sales assumption, {Math.round(a.fees * 100)}% effective fees,
          {" "}{formatMoney(a.cost_per_piece)} operating allowance per usable piece, and purchase cost with shipping. Usable-piece shares are planning estimates based on grade, not inspected counts.
        </li>
        <li>Each recommended box must meet your ROI target and avoid a loss when only half your planned pieces sell, at the lower of its lowest retained sold price and resale estimate, after operating costs on those pieces.</li>
        <li>It prefers researched selling-window matches among qualifying boxes, then incremental return, and takes another box of a kind only while what you sell of it over the selling window can absorb it. It checks the whole cart’s shipping. It also tests pairs that unlock free shipping. This is a greedy selection; it doesn’t guarantee the best possible combination.</li>
        <li>
          Raghouse shipping is FedEx Ground from Phoenix to {snapshot.destination_zip}, Thrift Vintage Fashion is UPS Ground from Hialeah, and PaperCrane is UPS Ground from each seller’s state
          ({snapshot.stores.papercrane?.origins ?? 0} states this check) plus card processing at Stripe’s standard rate, which PaperCrane does not publish; each store is
          scaled to what it charged your recorded orders ({Object.entries(snapshot.shipping.factors).map(([store, factor]) => `${storeName(store)} ${Math.round(factor * 100)}%`).join(", ")} of list).
          {" "}{Object.keys(snapshot.shipping.factors).map((store) => shippingNote(storeName(store), snapshot.shipping.calibration[store])).filter(Boolean).join(" ")}
          {" "}Correct a box’s shipping under Boxes you bought once the store charges it, and the estimates follow.
        </li>
        <li>Each supplier also gets its five best boxes judged one at a time, so there are always options to compare even when few boxes pass every check.</li>
        <li>Anteroom never buys. The cart buttons only fill a cart for you to check and pay.</li>
        <li>Inspect supplier photos and grade notes before buying. Check sale sources against the likely brand, era, condition and garment mix. Confirm final shipping and tax at checkout; a wholesale box is not an inspected set of identical products.</li>
      </ul>
    </SourcingDetails>
  );
}
