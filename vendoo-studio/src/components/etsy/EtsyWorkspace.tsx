import { useState } from "react";
import { Tabs } from "@base-ui/react/tabs";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2Icon, ExternalLinkIcon, RefreshCwIcon, Settings2Icon } from "lucide-react";
import { api } from "../../api/client";
import { AdSpend } from "../AdSpend";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { SaleCalendar } from "../SaleCalendar";
import { DirectConnectionRow } from "../settings/ConnectionsSettings";
import { PageError, PageNote, Panel } from "../WorkspacePage";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "../ui/dialog";
import { EtsyDashboard, EtsyListings, EtsyOrders, type OrderPeriod } from "./SellerPanels";

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

  return <div className="flex min-w-0 flex-col gap-6">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">Sales channel</p>
        <div className="flex items-center gap-3"><MarketplaceLogo id="etsy" label="Etsy" size={28} /><h2 className="text-2xl font-semibold tracking-tight">Etsy</h2></div>
        <p className="text-sm text-muted-foreground">Your shop, orders, listings, and marketing.</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {connected ? <span className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs"><CheckCircle2Icon className="size-3.5 text-success-foreground" />{account?.label || "Connected"}</span> : null}
        {connected ? <Button variant="ghost" size="sm" aria-label="Refresh Etsy" onClick={() => void queryClient.invalidateQueries({ queryKey: ["etsy-workspace"] })}><RefreshCwIcon />Refresh</Button> : null}
        <Button variant="outline" size="sm" onClick={() => setConnectionOpen(true)}><Settings2Icon />{connected ? "Connection" : account?.pending ? "Finish sign-in" : "Connect Etsy"}</Button>
      </div>
    </header>
    {connection.error ? <PageError message={connection.error.message} /> : null}
    <Tabs.Root value={tab} onValueChange={(value) => setTab(value as Tab)}>
      <Tabs.List aria-label="Etsy workspace" className="mb-6 grid grid-cols-4 gap-1 border-b border-border">
        {TABS.map((item) => <Tabs.Tab key={item} value={item} disabled={!connected && (item === "Orders" || item === "Listings")}
          className="min-h-11 cursor-pointer rounded-t-md border-b-2 border-transparent px-1 py-2 text-xs font-medium sm:px-2 sm:text-sm text-muted-foreground outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:opacity-50 data-[active]:border-orange-600 data-[active]:text-foreground">
          {item}
        </Tabs.Tab>)}
      </Tabs.List>
      <Tabs.Panel value={tab}>
        <div className="flex min-w-0 flex-col gap-6">
          {tab === "Marketing" ? <EtsyMarketingPanel onOpenListing={onOpenListing} /> : !connected ? (
            <Panel divided={false} className="space-y-3 p-5">
              <h3 className="font-medium">{connection.isPending ? "Checking your connection…" : "Connect your Etsy shop"}</h3>
              <p className="text-sm text-muted-foreground">Connect Etsy to see live shop details, orders, and listings.</p>
              {!connection.isPending ? <Button onClick={() => setConnectionOpen(true)}>Connect Etsy</Button> : null}
            </Panel>
          ) : <>
            {tab !== "Listings" ? <>
              <div className="flex flex-wrap items-end justify-between gap-4">
                <div><h3 className="text-lg font-semibold">{tab === "Dashboard" ? "Shop overview" : "Shop orders"}</h3><p className="mt-1 text-xs text-muted-foreground">Order creation dates in UTC.</p></div>
                <div className="grid grid-cols-2 gap-3 text-xs text-muted-foreground">
                  <label className="space-y-1">From<Input nativeInput type="date" aria-label="Etsy orders from" min="2000-01-01" max={end} value={start} onChange={(event) => setStart(event.target.value)} /></label>
                  <label className="space-y-1">To<Input nativeInput type="date" aria-label="Etsy orders to" min={start} max={today} value={end} onChange={(event) => setEnd(event.target.value)} /></label>
                </div>
              </div>
              {!period.valid ? <PageNote>Choose valid dates through today, up to one year at a time.</PageNote> : null}
            </> : null}
            {tab === "Dashboard" ? <EtsyDashboard shop={shop.data} shopError={shop.error} scope={scope} period={period} onOpenOrders={() => setTab("Orders")} />
              : tab === "Orders" ? <EtsyOrders scope={scope} period={period} />
                : <EtsyListings scope={scope} onOpenListing={onOpenListing} />}
          </>}
        </div>
      </Tabs.Panel>
    </Tabs.Root>
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
      <div><h3 className="text-lg font-semibold">Sales and advertising</h3><p className="mt-1 text-sm text-muted-foreground">Plan sales here, set them up in Etsy, and import your Etsy Ads results.</p></div>
      <a className="inline-flex items-center gap-1 text-sm underline" href="https://www.etsy.com/your/shops/me/advertising" target="_blank" rel="noreferrer">Open Etsy Ads<ExternalLinkIcon className="size-3.5" /></a>
    </div>
    <SaleCalendar marketplace="etsy" onOpenListing={onOpenListing} />
    <AdSpend marketplace="etsy" />
  </>;
}
