import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2Icon, ExternalLinkIcon, RefreshCwIcon, Settings2Icon } from "lucide-react";
import { api } from "../../api/client";
import { AdSpend } from "../AdSpend";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { SaleCalendar } from "../SaleCalendar";
import { WorkspaceTabs } from "../ebay/WorkspaceTabs";
import "../ebay/ebay.css";
import { DirectConnectionRow } from "../settings/ConnectionsSettings";
import { PageError, PageNote } from "../WorkspacePage";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "../ui/dialog";
import { EtsyDashboard, EtsyListings, EtsyOrders, EtsySaleAlerts, type OrderPeriod } from "./SellerPanels";

const TABS = ["Dashboard", "Orders", "Listings", "Marketing"] as const;
type Tab = (typeof TABS)[number];

export function EtsyWorkspace({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const [tab, setTab] = useState<Tab>("Dashboard");
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const [start, setStart] = useState(() => new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(today);
  const queryClient = useQueryClient();
  const connection = useQuery({
    queryKey: ["direct-connections"], queryFn: api.directConnections.status,
    refetchInterval: (query) => query.state.data?.accounts.etsy.pending ? 2000 : 15000,
  });
  const account = connection.data?.accounts.etsy;
  const connected = Boolean(account?.connected);
  const scope = `${account?.label || ""}:${account?.connectedAt || ""}`;
  const shop = useQuery({ queryKey: ["etsy-workspace", "shop", scope], queryFn: api.etsy.shop,
    enabled: connected, retry: false, staleTime: 30000 });
  const period: OrderPeriod = {
    start, end,
    valid: Boolean(start >= "2000-01-01" && end <= today && start <= end
      && (Date.parse(end) - Date.parse(start)) / 86400000 <= 366),
  };

  return <div className="ebay-workspace" data-marketplace="etsy">
    <header className="ebay-page-heading">
      <div><p className="ebay-eyebrow mb-3">Sales channel</p>
        <div className="flex items-center gap-3"><MarketplaceLogo id="etsy" label="Etsy" size={28} /><h2>Etsy</h2></div>
        <p>Your shop, orders, listings, and marketing.</p></div>
      <div className="ebay-connection">
        {connected ? <span className="ebay-status"><CheckCircle2Icon aria-hidden="true" />{account?.label || "Connected"}</span> : null}
        {connected ? <Button variant="ghost" aria-label="Refresh Etsy" onClick={() => void queryClient.invalidateQueries({ queryKey: ["etsy-workspace"] })}><RefreshCwIcon />Refresh</Button> : null}
        <Button variant="outline" onClick={() => setConnectionOpen(true)}><Settings2Icon />{connected ? "Connection" : account?.pending ? "Finish sign-in" : "Connect Etsy"}</Button>
      </div>
    </header>
    {connection.error ? <PageError message={connection.error.message} /> : null}
    <WorkspaceTabs label="Etsy workspace" main items={TABS} value={tab} onChange={(value) => setTab(value as Tab)}
      disabled={connected ? [] : ["Orders", "Listings"]}>
      <div className="ebay-tab-content" key={tab}>
        {tab === "Marketing" ? <EtsyMarketingPanel onOpenListing={onOpenListing} /> : !connected ? (
          <div className="ebay-data-panel space-y-4">
            <h2>{connection.isPending ? "Checking your connection…" : "Connect your Etsy shop"}</h2>
            <p className="text-muted-foreground">Connect Etsy to see live shop details, orders, and listings.</p>
            {!connection.isPending ? <Button onClick={() => setConnectionOpen(true)}>Connect Etsy</Button> : null}
          </div>
        ) : <>
          {tab !== "Listings" ? <>
            <div className="ebay-dashboard-toolbar">
              <div className="ebay-tab-intro"><p className="ebay-eyebrow">{tab === "Dashboard" ? "Performance" : "Orders"}</p><h2>{tab === "Dashboard" ? "Shop overview" : "Shop orders"}</h2><p>Order creation dates in UTC.</p></div>
              <div className="ebay-date-range">
                <label>From<Input nativeInput type="date" aria-label="Etsy orders from" min="2000-01-01" max={end} value={start} onChange={(event) => setStart(event.target.value)} /></label>
                <label>To<Input nativeInput type="date" aria-label="Etsy orders to" min={start} max={today} value={end} onChange={(event) => setEnd(event.target.value)} /></label>
              </div>
            </div>
            {!period.valid ? <PageNote>Choose valid dates through today, up to one year at a time.</PageNote> : null}
          </> : null}
          {tab === "Dashboard" ? <><EtsyDashboard shop={shop.data} shopError={shop.error} scope={scope} period={period} onOpenOrders={() => setTab("Orders")} /><EtsySaleAlerts onOpenListing={onOpenListing} /></>
            : tab === "Orders" ? <EtsyOrders scope={scope} period={period} />
              : <EtsyListings scope={scope} onOpenListing={onOpenListing} />}
        </>}
      </div>
    </WorkspaceTabs>
    <Dialog open={connectionOpen} onOpenChange={setConnectionOpen}>
      <DialogPopup className="sm:max-w-xl">
        <DialogHeader><DialogTitle>Etsy connection</DialogTitle><DialogDescription>Use your connected Etsy shop for live shop details, orders, listings, and sale alerts.</DialogDescription></DialogHeader>
        <DialogPanel><DirectConnectionRow marketplace={{ id: "etsy", name: "Etsy", what: "Connect the Etsy shop you want to manage in Anteroom." }} account={account} /></DialogPanel>
        <DialogFooter><Button variant="outline" onClick={() => setConnectionOpen(false)}>Close</Button></DialogFooter>
      </DialogPopup>
    </Dialog>
  </div>;
}

export function EtsyMarketingPanel({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  return <>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="ebay-tab-intro"><h2>Sales and advertising</h2><p>Plan sales here, set them up in Etsy, and import your Etsy Ads results.</p></div>
      <a className="inline-flex items-center gap-1 text-sm underline" href="https://www.etsy.com/your/shops/me/advertising" target="_blank" rel="noreferrer">Open Etsy Ads<ExternalLinkIcon className="size-3.5" /></a>
    </div>
    <SaleCalendar marketplace="etsy" onOpenListing={onOpenListing} />
    <AdSpend marketplace="etsy" />
  </>;
}
