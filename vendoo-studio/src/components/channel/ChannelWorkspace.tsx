import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLinkIcon } from "lucide-react";
import { api } from "../../api/client";
import { AdSpend } from "../AdSpend";
import { formatDays, formatMoney } from "../analyticsFormat";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { marketplaceName } from "../marketplaceNames";
import { SaleCalendar } from "../SaleCalendar";
import { PageError, PageNote } from "../WorkspacePage";
import { Input } from "../ui/input";
import { WorkspaceTabs } from "../ebay/WorkspaceTabs";
import "../ebay/ebay.css";

/** Marketplaces Studio reaches through Vendoo and the extension rather than a direct API connection. */
export type ChannelMarketplace = "poshmark" | "depop" | "sellwild";

const TABS = ["Dashboard", "Marketing"] as const;
type Tab = (typeof TABS)[number];

const CHANNELS: Record<ChannelMarketplace, { summary: string; url: string; marketing: string }> = {
  poshmark: { summary: "Sales and Promoted Closet spend.", url: "https://poshmark.com", marketing: "Record Promoted Closet spend and see what it brought in." },
  depop: { summary: "Sales and sale planning.", url: "https://www.depop.com", marketing: "Plan sales from when your Depop buyers shop, then set them up in Depop." },
  sellwild: { summary: "Sales and sale planning.", url: "https://sellwild.com", marketing: "Plan sales from when your Sellwild buyers shop, then set them up in Sellwild." },
};

/** One marketplace's workspace, laid out like eBay's: a channel heading, then the seller tabs. */
export function ChannelWorkspace({ marketplace, onOpenListing }: {
  marketplace: ChannelMarketplace; onOpenListing: (id: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("Dashboard");
  const name = marketplaceName(marketplace);
  const channel = CHANNELS[marketplace];
  return <div className="ebay-workspace" data-marketplace={marketplace}>
    <header className="ebay-page-heading">
      <div><p className="ebay-eyebrow mb-3">Sales channel</p>
        <div className="flex items-center gap-3"><MarketplaceLogo id={marketplace} label={name} size={28} /><h2>{name}</h2></div>
        <p>{channel.summary}</p></div>
      <div className="ebay-connection">
        <a className="inline-flex items-center gap-1 text-sm underline" href={channel.url} target="_blank" rel="noreferrer">Open {name}<ExternalLinkIcon className="size-3.5" aria-hidden="true" /></a>
      </div>
    </header>
    <WorkspaceTabs label={`${name} workspace`} main items={TABS} value={tab} onChange={(value) => setTab(value as Tab)}>
      <div className="ebay-tab-content" key={tab}>
        {tab === "Dashboard" ? <ChannelDashboard marketplace={marketplace} /> : <ChannelMarketing marketplace={marketplace} onOpenListing={onOpenListing} />}
      </div>
    </WorkspaceTabs>
  </div>;
}

/** Poshmark tracks Promoted Closet spend; Depop and Sellwild plan sales on the calendar. */
export function ChannelMarketing({ marketplace, onOpenListing }: {
  marketplace: ChannelMarketplace; onOpenListing: (id: string) => void;
}) {
  return <>
    <div className="ebay-tab-intro"><h2>{marketplace === "poshmark" ? "Promoted Closet" : "Sales and promotions"}</h2><p>{CHANNELS[marketplace].marketing}</p></div>
    {marketplace === "poshmark" ? <AdSpend marketplace="poshmark" /> : <SaleCalendar marketplace={marketplace} onOpenListing={onOpenListing} />}
  </>;
}

function isoDay(date: Date) {
  return date.toISOString().slice(0, 10);
}

/** Sales Studio has recorded for this marketplace, from the same totals as Analytics. */
export function ChannelDashboard({ marketplace }: { marketplace: ChannelMarketplace }) {
  const [today] = useState(() => isoDay(new Date()));
  const [start, setStart] = useState(() => isoDay(new Date(Date.now() - 29 * 86400000)));
  const [end, setEnd] = useState(today);
  const valid = Boolean(start && end && start <= end && end <= today);
  const analytics = useQuery({
    queryKey: ["analytics", "custom", start, end],
    queryFn: () => api.analytics.get("custom", { start, end }),
    enabled: valid,
  });
  const sales = analytics.data?.marketplaces.find((row) => row.id === marketplace);
  const name = marketplaceName(marketplace);
  const count = sales?.count ?? 0;
  const loaded = valid && analytics.data != null;
  const show = (value: string) => loaded ? value : "—";
  return <>
    <div className="ebay-dashboard-toolbar">
      <div className="ebay-tab-intro"><p className="ebay-eyebrow">Performance</p><h2>Sales overview</h2><p>Your {name} sales recorded in Studio.</p></div>
      <div className="ebay-date-range">
        <label>From<Input nativeInput type="date" aria-label={`${name} sales from`} value={start} max={end} onChange={(event) => setStart(event.target.value)} /></label>
        <label>To<Input nativeInput type="date" aria-label={`${name} sales to`} value={end} min={start} max={today} onChange={(event) => setEnd(event.target.value)} /></label>
      </div>
    </div>
    {!valid ? <PageNote>Choose a valid start and end date through today.</PageNote> : null}
    {analytics.error ? <PageError message={analytics.error.message} /> : null}
    <dl className="ebay-metrics">
      <div className="ebay-metric"><dt>Sales</dt><dd>{show(String(count))}</dd><p>Items sold on {name}</p></div>
      <div className="ebay-metric"><dt>Revenue</dt><dd>{show(formatMoney(sales?.revenue ?? 0))}</dd><p>{sales?.average_price != null ? `${formatMoney(sales.average_price)} average sale` : "Sale prices before fees"}</p></div>
      <div className="ebay-metric"><dt>Profit</dt><dd>{show(sales?.profit != null ? formatMoney(sales.profit) : count ? "Not available" : formatMoney(0))}</dd>
        <p>{count && sales ? `${sales.profit_known} of ${count} ${count === 1 ? "sale has" : "sales have"} a recorded cost` : "After fees and item costs"}</p></div>
    </dl>
    {loaded && sales?.median_days != null ? <p className="text-xs text-muted-foreground">Typical time to sell: {formatDays(sales.median_days)}</p> : null}
  </>;
}
